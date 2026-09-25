import type { TreeNode } from "@/types";

/** Depth-first search for a node by key. */
export function findNode(tree: TreeNode[], key: string): TreeNode | null {
  for (const node of tree) {
    if (node.key === key) return node;
    const found = findNode(node.children, key);
    if (found) return found;
  }
  return null;
}

/**
 * The tree keys the client needs, from the tree it was handed.
 *
 * There is deliberately no `caseKeyFor` here any more. It used to join a case id to a
 * node, and the join is not a function of the id: a suite lists the same step six
 * times, so the third row and the first row answer the same question — the lookup
 * returned the first and the reviewer clicked a row that opened somebody else's step.
 * The queue ships the node key of each row now, decided by the walk that painted it,
 * and a helper that guessed would be a second answer to a question that is settled.
 */
export function flatten(tree: TreeNode[]): TreeNode[] {
  const out: TreeNode[] = [];
  const walk = (nodes: TreeNode[]) => {
    for (const node of nodes) {
      out.push(node);
      walk(node.children);
    }
  };
  walk(tree);
  return out;
}

/** The chain from the root down to `key`, inclusive; empty when the key is unknown. */
export function ancestorsOf(tree: TreeNode[], key: string): TreeNode[] {
  const path: TreeNode[] = [];
  const walk = (nodes: TreeNode[], trail: TreeNode[]): boolean => {
    for (const node of nodes) {
      const next = [...trail, node];
      if (node.key === key) {
        path.push(...next);
        return true;
      }
      if (walk(node.children, next)) return true;
    }
    return false;
  };
  walk(tree, []);
  return path;
}

/** Every node of a kind, in tree order. */
export function nodesOfKind(tree: TreeNode[], kind: TreeNode["kind"]): TreeNode[] {
  return flatten(tree).filter((node) => node.kind === kind);
}
