"""Unicode lexical normalization and a small, explicit Boolean query grammar."""
import re
import unicodedata
from dataclasses import dataclass


def normalize(text: str) -> str:
    return unicodedata.normalize("NFKC", text).casefold()


# Urdu / mixed Urdu–English text (Urdu Sukhan: Urdu script, English words in Latin, no punctuation). Arabic-script
# letter variants that ASR output, keyboards and pasted text mix are folded to the Urdu letter, and Arabic-Indic /
# Urdu digits to ASCII, so a query matches however the word was typed. Only the search tokens change; stored text
# keeps the letters it was written with.
FOLD = str.maketrans({
    "\u064a": "\u06cc",   # ي Arabic yeh       → ی Urdu (Farsi) yeh
    "\u0649": "\u06cc",   # ى alef maksura     → ی
    "\u0643": "\u06a9",   # ك Arabic kaf       → ک keheh
    "\u0647": "\u06c1",   # ه Arabic heh       → ہ heh goal (ھ do-chashmi stays distinct)
    **{chr(0x0660 + i): str(i) for i in range(10)},   # ٠–٩
    **{chr(0x06F0 + i): str(i) for i in range(10)},   # ۰–۹
})
# Inside a word but not part of its spelling for matching: zero-width non-joiner / joiner and tatweel (kashida).
JOINERS = {"\u200c", "\u200d", "\u0640"}


def _script(char: str) -> str | None:
    """'arabic' or 'latin' for letters of those scripts; None for digits, marks and other scripts."""
    code = ord(char)
    if 0x0600 <= code <= 0x06FF or 0x0750 <= code <= 0x077F or 0x08A0 <= code <= 0x08FF:
        return "arabic" if unicodedata.category(char)[0] == "L" else None
    if code < 0x0250 and char.isalpha():
        return "latin"
    return None


def tokenize(text: str) -> list[str]:
    # Combining marks are retained, including Urdu vowel marks. No stopwords. A change of script between letters
    # ("depressionکی") also ends a word, since ASR output can omit the space between an English and an Urdu word.
    words, current, script = [], [], None
    for char in normalize(text).translate(FOLD):
        if char in JOINERS:
            continue
        if unicodedata.category(char)[0] in "LNM" or char == "_":
            kind = _script(char)
            if kind and script and kind != script and current:
                words.append("".join(current))
                current = []
            script = kind or script
            current.append(char)
        elif current:
            words.append("".join(current))
            current, script = [], None
        else:
            script = None
    if current:
        words.append("".join(current))
    return words


class QuerySyntaxError(ValueError):
    pass


@dataclass(frozen=True)
class Atom:
    terms: tuple[str, ...]

    def matches(self, tokens: list[str]) -> bool:
        n = len(self.terms)
        return any(tuple(tokens[i:i + n]) == self.terms for i in range(len(tokens) - n + 1))


@dataclass(frozen=True)
class Expression:
    operator: str
    children: tuple

    def matches(self, tokens: list[str]) -> bool:
        if self.operator == "NOT":
            return not self.children[0].matches(tokens)
        if self.operator == "AND":
            return all(child.matches(tokens) for child in self.children)
        return any(child.matches(tokens) for child in self.children)


@dataclass(frozen=True)
class ParsedQuery:
    expression: Atom | Expression | None
    positive_terms: tuple[str, ...]
    explicit: bool

    def matches(self, tokens: list[str]) -> bool:
        return self.expression is not None and self.expression.matches(tokens)


def parse_query(query: str) -> ParsedQuery:
    """Uppercase operators; NOT > AND > OR. Adjacent terms imply OR.

    Plain natural-language queries are soft queries for dense retrieval. Quoted
    phrases and explicit operators are hard filters for every retrieval method.
    Query NOT excludes text; it does not detect linguistic assertion context.
    """
    if len(query) > 2000:
        raise QuerySyntaxError("Search queries must be at most 2000 characters.")
    raw = re.findall(r'"[^"\n]*"|[()]|[^\s()"]+|"', query)
    items = []
    explicit = False
    for item in raw:
        if item == '"':
            raise QuerySyntaxError("Close the quoted phrase.")
        if item in {"AND", "OR", "NOT", "(", ")"}:
            explicit = True
            items.append(item)
        else:
            terms = tokenize(item)
            if item.startswith('"'):
                explicit = True
                if not terms:
                    raise QuerySyntaxError("Quoted phrases must contain searchable text.")
                items.append(Atom(tuple(terms)))
            else:
                items.extend(Atom((term,)) for term in terms)
    if not items:
        return ParsedQuery(None, (), explicit)
    if len(items) > 256:
        raise QuerySyntaxError("Search query has too many terms or operators.")
    position = 0

    def unary():
        nonlocal position
        if position >= len(items):
            raise QuerySyntaxError("An operator needs a search term.")
        item = items[position]
        position += 1
        if item == "NOT":
            return Expression("NOT", (unary(),))
        if item == "(":
            value = disjunction()
            if position >= len(items) or items[position] != ")":
                raise QuerySyntaxError("Close the parenthesized expression.")
            position += 1
            return value
        if not isinstance(item, Atom):
            raise QuerySyntaxError("Expected a search term or quoted phrase.")
        return item

    def conjunction():
        nonlocal position
        value = unary()
        while position < len(items) and items[position] in ("AND", "NOT"):
            # A NOT following a term implies AND NOT.
            if items[position] == "AND":
                position += 1
            value = Expression("AND", (value, unary()))
        return value

    def disjunction():
        nonlocal position
        value = conjunction()
        while position < len(items) and items[position] != ")":
            if items[position] == "OR":
                position += 1
            value = Expression("OR", (value, conjunction()))
        return value

    expression = disjunction()
    if position != len(items):
        raise QuerySyntaxError("Unexpected closing parenthesis.")
    positives = []

    def collect(node, negative=False):
        if isinstance(node, Atom):
            if not negative:
                positives.extend(node.terms)
        else:
            for child in node.children:
                collect(child, not negative if node.operator == "NOT" else negative)

    collect(expression)
    return ParsedQuery(expression, tuple(dict.fromkeys(positives)), explicit)
