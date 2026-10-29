"""Render the canonical contribution guide with site-local links."""
from pathlib import Path


def on_page_markdown(markdown, page, config, files):
    if page.file.src_uri == "contributing.md":
        root = Path(config.config_file_path).parent
        return (root / "CONTRIBUTING.md").read_text().replace(
            "](docs/", "]("
        )
    return markdown
