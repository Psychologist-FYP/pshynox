"""Unicode lexical normalization and a small, explicit Boolean query grammar."""
import re
import unicodedata
from dataclasses import dataclass


def normalize(text: str) -> str:
    return unicodedata.normalize("NFKC", text).casefold()


def tokenize(text: str) -> list[str]:
    # Combining marks are retained, including Urdu vowel marks. No stopwords.
    words, current = [], []
    for char in normalize(text):
        if unicodedata.category(char)[0] in "LNM" or char == "_":
            current.append(char)
        elif current:
            words.append("".join(current))
            current = []
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
