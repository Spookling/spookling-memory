"""Spreading activation (research/37 B, E3): Personalized PageRank over turns, events, entities and classes.

Haveliwala Eq. 5: r = (1 - a) M r + a p, M column-stochastic over the weighted, undirected edges; solved by
iteration (the error falls by (1 - a) per step). a = 0.5 as HippoRAG 2 (the walk restarts every other step, so it
mostly smooths over one or two hops). Seeds are the entities and classes the question names, each weighted by an
idf over events (the fan effect: a node linked to everything spreads little), plus the best dense event units at
0.05 (HippoRAG 2's passage seeds). Collins & Loftus p415: an intersection must be checked, so a unit reached by
association alone is returned only if it shares an entity or class with a seed or with an event that holds one.

The graph is built once per full sleep and is immutable, so forks share it.
"""
from __future__ import annotations

import math

import numpy as np
from scipy import sparse

RESTART = 0.5
ITERS = 30
TOL = 1e-10
W_ROLE = {"place": 1.0, "with": 1.0, "object": 1.0, "actor": 0.5}
W_CLASS = 0.5
W_LINK = {"same_as": 1.0, "same_day": 0.5, "adjacent": 0.5, "before": 0.25}
DENSE_SEED = 0.05


def ppr(M: sparse.csc_matrix, p: np.ndarray, restart: float = RESTART, iters: int = ITERS, tol: float = TOL) -> np.ndarray:
    """Personalized PageRank by power iteration (Haveliwala Eq. 5). p is normalised to sum 1."""
    p = p / p.sum()
    r = p.copy()
    for _ in range(iters):
        nxt = (1 - restart) * (M @ r) + restart * p
        if np.abs(nxt - r).sum() < tol:
            return nxt
        r = nxt
    return r


def column_stochastic(n: int, edges: list[tuple[int, int, float]]) -> sparse.csc_matrix:
    """Undirected weighted edges -> M with m_ij = w_ij / sum_k w_kj (a dangling column keeps its mass at home)."""
    rows, cols, vals = [], [], []
    for a, b, w in edges:
        rows += [a, b]
        cols += [b, a]
        vals += [w, w]
    A = sparse.coo_matrix((vals, (rows, cols)), shape=(n, n)).tocsc()
    out = np.asarray(A.sum(axis=0)).ravel()
    dangling = np.where(out == 0)[0]
    if len(dangling):
        A = A + sparse.coo_matrix((np.ones(len(dangling)), (dangling, dangling)), shape=(n, n)).tocsc()
        out[dangling] = 1.0
    return (A @ sparse.diags(1.0 / out)).tocsc()


class Graph:
    """Nodes: turn units, event units, entities, classes. Edges: event-turn, event-entity (by role), event-class,
    event-event (same_as, same_day, adjacent)."""

    def __init__(self, search, store):
        self.n_turn = len(search.units)
        self.n_event = len(search.eunits)
        turn_of = {(u.session, u.lo): i for i, u in enumerate(search.units)}
        for i, u in enumerate(search.units):  # a unit spans lo..hi; its user turn is at lo
            for p in range(u.lo, u.hi + 1):
                turn_of.setdefault((u.session, p), i)
        ent_ids = sorted({e for info in search.einfo for e in info["ents"]})
        cls_ids = sorted({c for info in search.einfo for c in info["classes"]})
        base = self.n_turn + self.n_event
        self.ent = {e: base + k for k, e in enumerate(ent_ids)}
        self.cls = {c: base + len(ent_ids) + k for k, c in enumerate(cls_ids)}
        self.n = base + len(ent_ids) + len(cls_ids)
        edges = []
        ev_node = {}
        for j, (u, info) in enumerate(zip(search.eunits, search.einfo, strict=True)):
            node = self.n_turn + j
            ev_node[info["row"]["event_id"]] = node
            t = turn_of.get((u.session, u.lo))
            if t is not None:
                edges.append((node, t, 1.0))
            for e, role in info["roles"]:
                edges.append((node, self.ent[e], W_ROLE.get(role, 0.5)))
            for c in info["classes"]:
                edges.append((node, self.cls[c], W_CLASS))
        for a, b, kind, w in (store.db.execute("SELECT a, b, kind, weight FROM event_links") if store else ()):
            if a in ev_node and b in ev_node:
                edges.append((ev_node[a], ev_node[b], W_LINK.get(kind, w)))
        self.M = column_stochastic(self.n, edges) if self.n else None
        deg = np.zeros(self.n)
        for a, b, _ in edges:
            deg[a] += 1
            deg[b] += 1
        self.deg = deg
        self.einfo = search.einfo

    def idf(self, node: int) -> float:
        """log(N_events / (1 + events linked)): a node every event shares seeds almost nothing."""
        return max(math.log((1 + self.n_event) / (1 + self.deg[node])), 0.05)

    def query(self, ents: set[str], classes: set[str], dense_events: list[int], cap: int) -> list[tuple[str, int]]:
        """Units ranked by activation from the question's entities and classes (plus the dense event seeds), at most
        `cap`, each kept only if it shares an entity or class with a seed or with an event holding a seed."""
        seeds = {self.ent[e]: self.idf(self.ent[e]) for e in ents if e in self.ent}
        seeds |= {self.cls[c]: self.idf(self.cls[c]) for c in classes if c in self.cls}
        if not seeds or self.M is None:
            return []
        p = np.zeros(self.n)
        for node, w in seeds.items():
            p[node] += w
        for j in dense_events[:20]:
            p[self.n_turn + j] += DENSE_SEED * max(seeds.values())
        r = ppr(self.M, p)
        # the typed path (C&L p415): seed entities and classes, and the entities of events that hold a seed entity
        reach_e, reach_c = set(ents), set(classes)
        for info in self.einfo:
            if info["ents"] & set(ents):
                reach_e |= info["ents"]
        ok_event = [bool(info["ents"] & reach_e or info["classes"] & reach_c) for info in self.einfo]
        turn_ok = {}
        for j, info in enumerate(self.einfo):
            if ok_event[j]:
                turn_ok[info["turn"]] = True
        order = np.argsort(-r[: self.n_turn + self.n_event], kind="stable")
        out = []
        for node in order:
            if r[node] <= 0:
                break
            if node < self.n_turn:
                if turn_ok.get(int(node)):
                    out.append(("t", int(node)))
            elif ok_event[node - self.n_turn]:
                out.append(("e", int(node - self.n_turn)))
            if len(out) >= cap:
                break
        return out
