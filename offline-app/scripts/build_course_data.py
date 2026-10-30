import json
from pathlib import Path

root = Path(__file__).resolve().parents[2]
library = json.loads((root / "data" / "library.json").read_text(encoding="utf-8"))

course_data = {
    "agents": {
        "cet4": "四级阅读与写作",
        "cet6": "六级阅读与写作",
        "postgrad": "考研阅读与写作",
    },
    "profiles": library.get("profiles", {}),
    "sources": [
        {
            "title": source.get("title", ""),
            "agent": source.get("agent", ""),
            "chunks": source.get("chunks", []),
        }
        for source in library.get("sources", [])
    ],
}

target = Path(__file__).resolve().parents[1] / "src" / "course-data.js"
target.write_text(
    "export const COURSE_DATA = "
    + json.dumps(course_data, ensure_ascii=False, indent=2)
    + ";\n",
    encoding="utf-8",
)
print(f"Wrote {target} with {len(course_data['sources'])} sources")
