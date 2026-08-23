"""Source-test to Source-function mapping utilities."""

from .models import LanguagePair, TestChunk

__all__ = [
    "LanguagePair",
    "SourceFunctionMappingAPI",
    "lookup_best_mapped_source_function",
    "lookup_mapped_source_functions",
    "lookup_source_function_mapping_record",
    "lookup_source_function_mapping_result",
    "TestChunk",
]


def __getattr__(name: str):
    if name == "SourceFunctionMappingAPI":
        from .source_function_mapping import SourceFunctionMappingAPI

        return SourceFunctionMappingAPI
    if name == "lookup_mapped_source_functions":
        from .source_function_mapping import lookup_mapped_source_functions

        return lookup_mapped_source_functions
    if name == "lookup_best_mapped_source_function":
        from .source_function_mapping import lookup_best_mapped_source_function

        return lookup_best_mapped_source_function
    if name == "lookup_source_function_mapping_record":
        from .source_function_mapping import lookup_source_function_mapping_record

        return lookup_source_function_mapping_record
    if name == "lookup_source_function_mapping_result":
        from .source_function_mapping import lookup_source_function_mapping_result

        return lookup_source_function_mapping_result
    raise AttributeError(name)
