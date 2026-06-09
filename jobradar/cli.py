"""CLI entrypoints — thin wrappers over pipeline functions (see DESIGN §4.9)."""
import sys
from pathlib import Path

from dotenv import load_dotenv

from jobradar.store import init_db, _DEFAULT_DB


def main():
    load_dotenv(".env.local")
    load_dotenv()

    command = sys.argv[1] if len(sys.argv) > 1 else None

    if command == "setup":
        from jobradar.setup_wizard import setup_wizard
        setup_wizard()
    elif command == "setup-notion":
        from jobradar.pipeline import setup_notion
        if len(sys.argv) < 3:
            print("Usage: python -m jobradar setup-notion <notion-page-url-or-id>")
            sys.exit(1)
        setup_notion(sys.argv[2])
    elif command == "profile":
        from jobradar.pipeline import generate_profile
        if len(sys.argv) < 3:
            print("Usage: python -m jobradar profile <path-to-cv.pdf|.txt|.md>")
            sys.exit(1)
        generate_profile(sys.argv[2])
    elif command == "detect":
        from jobradar.pipeline import detect_companies
        force = "--force" in sys.argv
        detect_companies(force=force)
    elif command == "discover":
        from jobradar.pipeline import discover
        db_path = Path(sys.argv[2]) if len(sys.argv) > 2 else _DEFAULT_DB
        discover(db_path=db_path)
    elif command == "run":
        from jobradar.pipeline import run
        db_path = Path(sys.argv[2]) if len(sys.argv) > 2 else _DEFAULT_DB
        run(db_path=db_path)
    elif command == "review":
        from jobradar.pipeline import review_export
        out = Path(sys.argv[2]) if len(sys.argv) > 2 else None
        review_export(output_path=out)
    elif command is None:
        init_db(_DEFAULT_DB)
        print(f"jobradar: DB ready at {_DEFAULT_DB}")
        print("Commands: setup, setup-notion, profile, detect, discover, run, review")
    else:
        print(f"Unknown command: {command}")
        print("Commands: setup, setup-notion, profile, detect, discover, run, review")
        sys.exit(1)


if __name__ == "__main__":
    main()
