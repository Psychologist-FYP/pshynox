"""Set-theoretic fuzzy retrieval primitives for the Week 6 demo.

This module is deliberately independent of the HTTP layer.  It accepts the
same transcript/document-shaped values used by the browse service and returns
plain dataclasses that can be adapted to an API contract later.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any, Literal, Mapping, Sequence

from services.retrieval.browse import _tokens


class FuzzyQueryError(ValueError):
    """Raised when a fuzzy query has invalid syntax."""


@dataclass(frozen=True)
class QueryNode:
    operator: str
    value: str | None = None
    children: tuple["QueryNode", ...] = ()

    def as_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"operator": self.operator}
        if self.value is not None:
            result["value"] = self.value
        if self.children:
            result["children"] = [child.as_dict() for child in self.children]
        return result


@dataclass(frozen=True)
class ParsedQuery:
    root: QueryNode
    terms: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {"tree": self.root.as_dict(), "terms": list(self.terms)}


@dataclass(frozen=True)
class FuzzyHit:
    """A fuzzy result ready to be mapped to EvidenceHit plus additive fields."""

    segment_id: str
    session_id: str
    start_ms: int
    end_ms: int
    excerpt: str
    score: float
    section: str
    query: str
    memberships: dict[str, float]
    parsed_query: dict[str, Any]


_LEXER = re.compile(r'"[^"\\]*(?:\\.[^"\\]*)*"|\(|\)|[^\s()]+')
_OPERATORS = {"AND", "OR", "NOT"}
NotMode = Literal["fuzzy", "strict-exclude"]


def _lex(query: str) -> list[str]:
    if not query.strip():
        raise FuzzyQueryError("Query must contain at least one term")
    tokens = _LEXER.findall(query)
    if "".join(tokens).replace(" ", "") == "":
        raise FuzzyQueryError("Query must contain at least one term")
    return tokens


class _Parser:
    def __init__(self, tokens: list[str]) -> None:
        self.tokens = tokens
        self.position = 0

    def current(self) -> str | None:
        return self.tokens[self.position] if self.position < len(self.tokens) else None

    def consume(self) -> str:
        token = self.current()
        if token is None:
            raise FuzzyQueryError("Unexpected end of query")
        self.position += 1
        return token

    def parse(self) -> QueryNode:
        root = self.parse_or()
        if self.current() is not None:
            raise FuzzyQueryError(f"Unexpected token: {self.current()}")
        return root

    def parse_or(self) -> QueryNode:
        node = self.parse_and()
        while self.current() is not None and self.current().upper() == "OR":
            self.consume()
            node = QueryNode("OR", children=(node, self.parse_and()))
        return node

    def parse_and(self) -> QueryNode:
        node = self.parse_unary()
        while True:
            token = self.current()
            if token is None or token.upper() == "OR" or token == ")":
                return node
            if token.upper() == "AND":
                self.consume()
            elif not (token == "(" or token.upper() == "NOT" or self._is_term(token)):
                raise FuzzyQueryError(f"Unexpected token: {token}")
            node = QueryNode("AND", children=(node, self.parse_unary()))

    def parse_unary(self) -> QueryNode:
        if self.current() is not None and self.current().upper() == "NOT":
            self.consume()
            return QueryNode("NOT", children=(self.parse_unary(),))
        return self.parse_primary()

    def parse_primary(self) -> QueryNode:
        token = self.consume()
        if token == "(":
            node = self.parse_or()
            if self.current() != ")":
                raise FuzzyQueryError("Missing closing parenthesis")
            self.consume()
            return node
        if token == ")":
            raise FuzzyQueryError("Unexpected closing parenthesis")
        if token.upper() in _OPERATORS:
            raise FuzzyQueryError(f"Operator {token.upper()} is missing an operand")
        if token.startswith('"'):
            if not token.endswith('"') or len(token) == 2:
                raise FuzzyQueryError("Unterminated or empty quoted phrase")
            token = token[1:-1].replace('\\"', '"').strip()
            if not token:
                raise FuzzyQueryError("Empty quoted phrase")
        return QueryNode("TERM", value=" ".join(_tokens(token)))

    @staticmethod
    def _is_term(token: str) -> bool:
        return token not in {"(", ")"} and token.upper() not in _OPERATORS


def parse(query: str) -> ParsedQuery:
    """Parse OR/AND/NOT queries, parentheses, phrases, and implicit AND."""

    root = _Parser(_lex(query)).parse()
    terms: list[str] = []

    def collect(node: QueryNode) -> None:
        if node.operator == "TERM" and node.value is not None:
            if node.value not in terms:
                terms.append(node.value)
        for child in node.children:
            collect(child)

    collect(root)
    return ParsedQuery(root=root, terms=tuple(terms))


def _value(document: Any, field: str, default: Any = None) -> Any:
    if isinstance(document, Mapping):
        return document.get(field, default)
    return getattr(document, field, default)


def _stem(token: str) -> str:
    for suffix in ("ing", "ed", "es", "s"):
        if len(token) > len(suffix) + 2 and token.endswith(suffix):
            return token[: -len(suffix)]
    return token


def _similarity(term: str, token: str) -> float:
    if term == token:
        return 1.0
    if min(len(term), len(token)) >= 3 and (token.startswith(term) or term.startswith(token)):
        return 0.8
    if _stem(term) == _stem(token):
        return 0.7
    if min(len(term), len(token)) >= 3 and (term in token or token in term):
        return 0.5
    return 0.0


def _idf(term: str, corpus: Sequence[Any] | None) -> float:
    if not corpus:
        return 1.0
    query_tokens = _tokens(term)
    if not query_tokens:
        return 0.0
    document_frequency = 0
    for document in corpus:
        tokens = set(_tokens(str(_value(document, "text", ""))))
        if all(any(_similarity(query, token) > 0 for token in tokens) for query in query_tokens):
            document_frequency += 1
    raw = math.log(1 + (len(corpus) - document_frequency + 0.5) / (document_frequency + 0.5))
    maximum = math.log(1 + (len(corpus) + 0.5) / 0.5)
    return min(1.0, raw / maximum) if maximum else 1.0


def membership(term: str, document: Any, corpus: Sequence[Any] | None = None) -> float:
    """Return a normalized lexical membership value in [0, 1]."""

    query_tokens = _tokens(term)
    document_tokens = _tokens(str(_value(document, "text", "")))
    if not query_tokens or not document_tokens:
        return 0.0
    similarities = [max((_similarity(query, token) for token in document_tokens), default=0.0) for query in query_tokens]
    if any(value == 0 for value in similarities):
        return 0.0
    frequency = sum(1 for token in document_tokens if any(_similarity(query, token) > 0 for query in query_tokens))
    tf = 1 - math.exp(-frequency)
    return round(min(1.0, min(similarities) * tf * _idf(term, corpus)), 6)


def context_window(segment_id: str, documents: Sequence[Any], radius: int = 2) -> list[Any]:
    """Return the segment and neighbouring segments in the same session."""

    if radius < 0:
        raise ValueError("radius must be non-negative")
    ordered = sorted(documents, key=lambda item: (_value(item, "session_id", ""), _value(item, "start_ms", 0), _value(item, "id", "")))
    index = next((i for i, item in enumerate(ordered) if _value(item, "id", _value(item, "segment_id")) == segment_id), None)
    if index is None:
        return []
    session_id = _value(ordered[index], "session_id")
    same_session = [item for item in ordered if _value(item, "session_id") == session_id]
    center = next(i for i, item in enumerate(same_session) if _value(item, "id", _value(item, "segment_id")) == segment_id)
    return same_session[max(0, center - radius) : center + radius + 1]


def _evaluate(node: QueryNode, document: Any, corpus: Sequence[Any], not_mode: NotMode, threshold: float) -> float:
    if node.operator == "TERM":
        return membership(node.value or "", document, corpus)
    if node.operator == "AND":
        return min(_evaluate(child, document, corpus, not_mode, threshold) for child in node.children)
    if node.operator == "OR":
        return max(_evaluate(child, document, corpus, not_mode, threshold) for child in node.children)
    child_score = _evaluate(node.children[0], document, corpus, not_mode, threshold)
    return 1 - child_score if not_mode == "fuzzy" else float(child_score < threshold)


def _breakdown(parsed: ParsedQuery, document: Any, corpus: Sequence[Any]) -> dict[str, float]:
    return {term: membership(term, document, corpus) for term in parsed.terms}


def fuzzy_search(
    query: str,
    documents: Sequence[Any],
    k: int = 10,
    not_mode: NotMode = "fuzzy",
    window: int = 0,
    threshold: float = 0.5,
) -> list[FuzzyHit]:
    """Rank documents using fuzzy set operators and optional session context."""

    if not_mode not in {"fuzzy", "strict-exclude"}:
        raise ValueError("not_mode must be 'fuzzy' or 'strict-exclude'")
    if k <= 0 or not documents:
        return []
    parsed = parse(query)
    results: list[FuzzyHit] = []
    for document in documents:
        segment_id = str(_value(document, "id", _value(document, "segment_id", "")))
        context = context_window(segment_id, documents, window) if window else [document]
        context_document = {"text": " ".join(str(_value(item, "text", _value(item, "excerpt", ""))) for item in context)}
        score = _evaluate(parsed.root, context_document, documents, not_mode, threshold)
        if not score:
            continue
        results.append(
            FuzzyHit(
                segment_id=segment_id,
                session_id=str(_value(document, "session_id", "")),
                start_ms=int(_value(document, "start_ms", 0)),
                end_ms=int(_value(document, "end_ms", 0)),
                excerpt=str(_value(document, "text", _value(document, "excerpt", ""))),
                score=round(min(1.0, max(0.0, score)), 6),
                section=str(_value(document, "section", "other")),
                query=query,
                memberships=_breakdown(parsed, context_document, documents),
                parsed_query=parsed.as_dict(),
            )
        )
    return sorted(results, key=lambda hit: (-hit.score, hit.start_ms, hit.segment_id))[:k]
