"""Application-owned skills: metadata discovery, bounded routing, lazy body loading."""
import hashlib
from pathlib import Path
import yaml

ROOT = Path(__file__).parent / "skills"
ROUTES = [("refund-handling", ("退款", "退货", "refund")), ("shipping-handling", ("物流", "快递", "shipping"))]

def discover():
    catalog = []
    for name in [r[0] for r in ROUTES] + ["support-triage"]:
        path = ROOT / name / "SKILL.md"
        with path.open(encoding="utf-8") as stream:
            if stream.readline().strip() != "---":
                raise ValueError("Missing skill frontmatter")
            lines = []
            for line in stream:
                if line.strip() == "---":
                    break
                lines.append(line)
            metadata = yaml.safe_load("".join(lines))
        if metadata["name"] != name or not metadata.get("description"):
            raise ValueError("Invalid skill metadata")
        catalog.append({"name": name, "description": metadata["description"]})
    return catalog

def select_skill(ticket):
    catalog = discover()
    name = next((name for name, terms in ROUTES if any(t in ticket.lower() for t in terms)), "support-triage")
    content = (ROOT / name / "SKILL.md").read_text(encoding="utf-8")
    body = content.split("---", 2)[2].strip()
    return {"name": name, "sha256": hashlib.sha256(content.encode()).hexdigest(), "instructions": body, "catalog": catalog}
