"""Tolerant parser for HTML, including HTML stored under an XML suffix."""

from .markup import MarkupParser


class HtmlParser(MarkupParser):
    markup_kind = "html"
    parser_name = "lxml-html"


__all__ = ["HtmlParser"]
