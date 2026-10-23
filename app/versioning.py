from difflib import SequenceMatcher


def norm(text):
    return " ".join(text.lower().split())


def word_diff(old, new):
    # small human readable diff, e.g. ["'40' -> '30'", "added 'Increment size was ...'"]
    a, b = old.split(), new.split()
    changes = []
    for op, i1, i2, j1, j2 in SequenceMatcher(None, a, b).get_opcodes():
        before, after = " ".join(a[i1:i2]), " ".join(b[j1:j2])
        if op == "replace":
            changes.append(f"'{before}' -> '{after}'")
        elif op == "delete":
            changes.append(f"removed '{before}'")
        elif op == "insert":
            changes.append(f"added '{after}'")
    return changes


def match_nodes(old_nodes, new_nodes):
    """Pair up nodes from the previous version with nodes in the new one.
    Returns {new_node: old_node} for the ones that matched."""
    matches = {}
    left = list(old_nodes)

    # pass 1: same section id and same heading. covers almost everything
    for new in new_nodes:
        for old in left:
            if old.section_id == new.section_id and norm(old.heading) == norm(new.heading):
                matches[new] = old
                left.remove(old)
                break

    # pass 2: section got renumbered but kept its heading.
    # only if the heading is unique on both sides, otherwise two "Error Codes" could get swapped
    unmatched_new = [n for n in new_nodes if n not in matches]
    for new in unmatched_new:
        same_old = [o for o in left if norm(o.heading) == norm(new.heading)]
        same_new = [n for n in unmatched_new if norm(n.heading) == norm(new.heading)]
        if len(same_old) == 1 and len(same_new) == 1:
            matches[new] = same_old[0]
            left.remove(same_old[0])

    return matches


def link_versions(old_nodes, new_nodes, version_number):
    # old_nodes is empty for the first version
    matches = match_nodes(old_nodes, new_nodes)

    for node in new_nodes:
        old = matches.get(node)
        if old is None:
            node.logical_id = f"v{version_number}:{node.section_id}"
            node.status = "new" if not old_nodes else "added"
        else:
            node.logical_id = old.logical_id
            node.prev_node_id = old.id
            node.status = "unchanged" if old.hash == node.hash else "changed"
