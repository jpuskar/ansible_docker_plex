"""Filter plugin to ensure YAML content starts with a document separator (---)."""


def ensure_yaml_doc_start(content):
    """Prefix YAML content with '---' if it doesn't already start with one."""
    text = str(content)
    if text.lstrip().startswith("---"):
        return text
    return "---\n" + text


class FilterModule:
    def filters(self):
        return {
            "ensure_yaml_doc_start": ensure_yaml_doc_start,
        }
