"""Register a curated set of safe built-in tools for the Studio palette.

Only read-only / compute tools are enabled by default — no email, SMS,
database-write, or shell tools. Users running their own instance can extend
this list.
"""

import logging

from genxai.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)


def register_studio_tools() -> int:
    """Register the curated tool set; returns how many tools are available."""
    from app.connectors_catalog import ConnectorActionTool
    from app.data_catalog import make_source_query_tool
    from app.mcp_registry import MCPActionTool
    from genxai.tools.builtin.computation.calculator import CalculatorTool
    from genxai.tools.builtin.computation.data_validator import DataValidatorTool
    from genxai.tools.builtin.computation.hash_generator import HashGeneratorTool
    from genxai.tools.builtin.computation.regex_matcher import RegexMatcherTool
    from genxai.tools.builtin.data.csv_processor import CSVProcessorTool
    from genxai.tools.builtin.data.dataset_tools import (
        DatasetQueryTool,
        DatasetWriteTool,
    )
    from genxai.tools.builtin.data.data_filter import DataFilterTool
    from genxai.tools.builtin.data.data_transformer import DataTransformerTool
    from genxai.tools.builtin.data.json_processor import JSONProcessorTool
    from genxai.tools.builtin.data.text_analyzer import TextAnalyzerTool
    from genxai.tools.builtin.data.xml_processor import XMLProcessorTool
    from genxai.tools.builtin.file.directory_scanner import DirectoryScannerTool
    from genxai.tools.builtin.file.excel_tools import ExcelReadTool, ExcelWriteTool
    from genxai.tools.builtin.file.file_reader import FileReaderTool
    from genxai.tools.builtin.file.file_store_tools import (
        FileContentTool,
        FileDownloadTool,
        FileWriteTool,
    )
    from genxai.tools.builtin.file.pdf_parser import PDFParserTool
    from genxai.tools.builtin.web.api_caller import APICallerTool
    from genxai.tools.builtin.web.html_parser import HTMLParserTool
    from genxai.tools.builtin.web.http_client import HTTPClientTool
    from genxai.tools.builtin.web.rss_reader import RSSReaderTool
    from genxai.tools.builtin.web.url_validator import URLValidatorTool
    from genxai.tools.builtin.web.web_scraper import WebScraperTool

    tool_classes = [
        ConnectorActionTool,
        RSSReaderTool,
        MCPActionTool,
        CalculatorTool,
        DataValidatorTool,
        HashGeneratorTool,
        RegexMatcherTool,
        CSVProcessorTool,
        DatasetWriteTool,
        DatasetQueryTool,
        DataTransformerTool,
        DataFilterTool,
        JSONProcessorTool,
        TextAnalyzerTool,
        XMLProcessorTool,
        DirectoryScannerTool,
        FileReaderTool,
        FileDownloadTool,
        FileWriteTool,
        FileContentTool,
        ExcelReadTool,
        ExcelWriteTool,
        PDFParserTool,
        APICallerTool,
        HTMLParserTool,
        HTTPClientTool,
        URLValidatorTool,
        WebScraperTool,
    ]

    for tool_class in tool_classes:
        try:
            tool = tool_class()
            if ToolRegistry.get(tool.metadata.name) is None:
                ToolRegistry.register(tool)
        except Exception as exc:
            logger.warning("Skipping tool %s: %s", tool_class.__name__, exc)

    from app.datascience import make_analysis_report_tool
    from app.ml import make_model_predict_tool, make_model_train_tool

    for factory in (
        make_source_query_tool,
        make_analysis_report_tool,
        make_model_train_tool,
        make_model_predict_tool,
    ):
        try:
            tool = factory()
            if ToolRegistry.get(tool.metadata.name) is None:
                ToolRegistry.register(tool)
        except Exception as exc:
            logger.warning("Skipping %s: %s", factory.__name__, exc)

    count = len(ToolRegistry.list_all())
    logger.info("Studio tool palette ready: %d tools", count)
    return count
