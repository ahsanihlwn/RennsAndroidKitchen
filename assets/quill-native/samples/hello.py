"""A small editor. A little less machinery."""

from dataclasses import dataclass
from pathlib import Path


@dataclass
class Workspace:
    name: str
    root: Path
    languages: tuple[str, ...] = ("Python", "Rust", "TypeScript")

    def greeting(self) -> str:
        # Unicode stays intact: Halo dunia 🦀
        return f"Welcome to {self.name}"


def main() -> None:
    workspace = Workspace("Quill Native", Path.cwd())
    print(workspace.greeting())

    settings = {
        "renderer": "Qt Quick",
        "engine": "Rust",
        "browser_processes": 0,
        "font_size": 15,
    }

    for key, value in settings.items():
        print(f"{key}: {value}")


if __name__ == "__main__":
    main()
