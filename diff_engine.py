"""Pure Python diff engine — no AI. Uses difflib to produce line-level diffs."""

import difflib


def compute_diff(old_text, new_text):
    """Compare two texts and return a list of diff lines.
    Each line is a dict: {type: 'add'|'remove'|'same'|'info', text: str}
    """
    if not old_text and not new_text:
        return []

    old_lines = (old_text or "").splitlines()
    new_lines = (new_text or "").splitlines()

    diff = difflib.unified_diff(old_lines, new_lines, lineterm="", n=3)
    result = []

    for line in diff:
        if line.startswith("+++") or line.startswith("---"):
            continue
        elif line.startswith("@@"):
            result.append({"type": "info", "text": line})
        elif line.startswith("+"):
            result.append({"type": "add", "text": line[1:]})
        elif line.startswith("-"):
            result.append({"type": "remove", "text": line[1:]})
        else:
            result.append({"type": "same", "text": line[1:] if line.startswith(" ") else line})

    return result


def diff_to_html(diff_lines):
    """Convert diff lines to an HTML string with color coding."""
    if not diff_lines:
        return '<p class="text-gray-400 italic">No differences found.</p>'

    html_parts = []
    for d in diff_lines:
        text = _escape(d["text"]) or "&nbsp;"
        if d["type"] == "add":
            html_parts.append(f'<div class="diff-add">{text}</div>')
        elif d["type"] == "remove":
            html_parts.append(f'<div class="diff-remove">{text}</div>')
        elif d["type"] == "info":
            html_parts.append(f'<div class="diff-info">{_escape(d["text"])}</div>')
        else:
            html_parts.append(f'<div class="diff-same">{text}</div>')

    return "\n".join(html_parts)


def side_by_side(old_text, new_text):
    """Produce side-by-side diff as two lists of {type, text} lines, aligned."""
    old_lines = (old_text or "").splitlines()
    new_lines = (new_text or "").splitlines()

    sm = difflib.SequenceMatcher(None, old_lines, new_lines)
    left = []
    right = []

    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            for i in range(i1, i2):
                left.append({"type": "same", "text": old_lines[i]})
            for j in range(j1, j2):
                right.append({"type": "same", "text": new_lines[j]})
        elif tag == "replace":
            old_chunk = list(range(i1, i2))
            new_chunk = list(range(j1, j2))
            max_len = max(len(old_chunk), len(new_chunk))
            for k in range(max_len):
                if k < len(old_chunk):
                    left.append({"type": "remove", "text": old_lines[old_chunk[k]]})
                else:
                    left.append({"type": "pad", "text": ""})
                if k < len(new_chunk):
                    right.append({"type": "add", "text": new_lines[new_chunk[k]]})
                else:
                    right.append({"type": "pad", "text": ""})
        elif tag == "delete":
            for i in range(i1, i2):
                left.append({"type": "remove", "text": old_lines[i]})
                right.append({"type": "pad", "text": ""})
        elif tag == "insert":
            for j in range(j1, j2):
                left.append({"type": "pad", "text": ""})
                right.append({"type": "add", "text": new_lines[j]})

    return left, right


def side_by_side_html(old_text, new_text):
    """Return HTML for a side-by-side diff table."""
    left, right = side_by_side(old_text, new_text)

    if not left and not right:
        return '<p class="text-gray-400 italic">No differences found.</p>'

    rows = []
    max_len = max(len(left), len(right))
    for i in range(max_len):
        l = left[i] if i < len(left) else {"type": "pad", "text": ""}
        r = right[i] if i < len(right) else {"type": "pad", "text": ""}
        l_cls = f"diff-{l['type']}"
        r_cls = f"diff-{r['type']}"
        l_text = _escape(l["text"]) or "&nbsp;"
        r_text = _escape(r["text"]) or "&nbsp;"
        rows.append(
            f'<tr><td class="{l_cls} diff-cell">{l_text}</td>'
            f'<td class="{r_cls} diff-cell">{r_text}</td></tr>'
        )

    return (
        '<table class="diff-table w-full">'
        '<thead><tr><th class="diff-header">Previous Filing</th>'
        '<th class="diff-header">Current Filing</th></tr></thead>'
        f'<tbody>{"".join(rows)}</tbody></table>'
    )


def _escape(text):
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
