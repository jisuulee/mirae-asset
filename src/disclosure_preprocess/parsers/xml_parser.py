"""DART XML parser."""

from .markup import MarkupParser


class XmlParser(MarkupParser):
    markup_kind = "xml"
    parser_name = "lxml-xml"


__all__ = ["XmlParser"]
