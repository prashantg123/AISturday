import logging

from common.visualize import print_vertex, print_connection
from common.util import reverse_complement
from graph.string_graph import AssemblyVertex, no_diedge

DEBUG = False

# ----------------------------------------------------------------------------

def contract_edges(g, E=None, store_ordering=False):
  if DEBUG:
    for w in g.vertices:
      for f in w.edges:
        assert w == f.v1 or w == f.v2

    all_V = set(g.vertices)
    for f in g.edges:
      assert f.v1 in all_V and f.v2 in all_V

    for e in g.edges:
      assert e in e.v1.edges
      assert e in e.v2.edges

  remove_loops(g)
  # remove_parallel_edges(g)

  if not E:
    candidate_edges = set(g.edges)
  else:
    candidate_edges = E

  n_contracted, n_seen, n_tot = 0, 0, len(g.edges)
  while candidate_edges:
    if n_seen % 5000 == 0:
      logging.info('%d/%d edges (%d contracted)' % (n_seen, n_tot, n_contracted))
    e = candidate_edges.pop()
    if can_be_contracted(e, g): 
      contract_edge(g, e, candidate_edges, store_ordering)
      n_contracted += 1
    n_seen += 1

  logging.info('%d/%d (%d contracted)' % (n_seen, n_tot, n_contracted))

  return n_contracted

def remove_loops(g):
  for e in g.edges:
    if e.v1 == e.v2:
      g.remove_edge(e)
      e.v1.head_edges.discard(e)
      e.v1.tail_edges.discard(e)

def remove_parallel_edges(g):
  visited_pairs = set()
  E = list(g.edges)
  for e in E:
    s = frozenset([e.v1, e.v2])
    if s not in visited_pairs:
      visited_pairs.add(s)
    else:
      g.remove_edge(e)
      e.v1.disconnect_edge(e)
      e.v2.disconnect_edge(e)

def can_be_contracted(e, g):
  v1, v2 = e.v1, e.v2

  # edge may have been deleted earlier
  # if e not in g.edges:
    # return False

  # we cannot contract loops:
  if v1 == v2: return False

  # some checks
  assert e in v1.edges
  assert e in v2.edges

  # an edge can be contracted if it connects v1, v2 at poles x, y
  # and it is the only edge at pole x in v1
  # and the only edge at pole y in v2

  if e.connection[v1] == 'H' and len(v1.head_edges) == 1:
    if e.connection[v2] == 'H' and len(v2.head_edges) == 1:
      return True
    elif e.connection[v2] == 'T' and len(v2.tail_edges) == 1:
      return True
  elif e.connection[v1] == 'T' and len(v1.tail_edges) == 1:
    if e.connection[v2] == 'H' and len(v2.head_edges) == 1:
      return True
    elif e.connection[v2] == 'T' and len(v2.tail_edges) == 1:
      return True

  return False

def contract_edge(g, e, E, store_ordering=False):
  if e.is_overlap_edge:
    contract_overlap_edge(g,e,E)
  elif e.is_scaffold_edge:
    v_new = contract_scaffold_edge(g,e,E)
    if store_ordering:
      v_new.set_contigs_from_vertices(e.v1, e.v2)
  else:
    raise ValueError('Invalid edge type found')

def contract_scaffold_edge(g, e, candidate_edges):
  v1, v2 = e.v1, e.v2

  # if there are any other edges parallel edges, delete them
  for f in v1.edges:
    assert v1 == f.v1 or v1 == f.v2
    if f != e and v2 == f.other_vertex(v1):
      g.remove_edge(f)
      candidate_edges.discard(f)

  assert e in v1.edges
  assert e in v2.edges

  # store set of edges that will be removed (for verificaiton later)
  good_E = [f for f in v1.edges if f != e] + [f for f in v2.edges if f != e]
  good_V = [f.other_vertex(v1) for f in v1.edges if f != e] \
         + [f.other_vertex(v2) for f in v2.edges if f != e]

  vg1 = (v1.id, e.connection[v1])
  vg2 = (v2.id, e.connection[v2])
  assert g._graph.has_edge(vg1, vg2)

  _orient_th(g, e, v1, v2)
  v1, v2 = e.v1, e.v2

  assert e.connection[v1] == 'T' and e.connection[v2] == 'H'

  orientation = e.orientation

  # build new vertex
  new_id = g.vertex_id_generator.get_id()

  # construct new sequence
  distance = max(0, e.distance)
  padding = 'N' * 10

  if orientation == 0:
    new_seq = v1.seq + padding + v2.seq
  elif orientation == 1:
    new_seq = v1.seq + padding + reverse_complement(v2.seq)
  else:
    raise Exception("ERROR: Incorrect orientation!")

  new_v = AssemblyVertex(new_id, new_seq)
  new_v.head_edges = v1.head_edges
  new_v.tail_edges = v2.tail_edges

  _merge_metadata(new_v, v1, v2, len(v1.seq) + len(padding))

  # insert new node:
  g.add_vertex(new_v)

  # correct edges incident to v1
  E = [f for f in v1.head_edges]
  for f in E:
    if f.v1 == v2 or f.v2 == v2:
      # this will create a loop, so remove that edge
      g.remove_edge(f)
      candidate_edges.discard(e)
    else:
      g.reconnect(f, v1, new_v)
    assert f.v1 != f.v2

  # correct edges incident to v2
  for f in v2.tail_edges:
    g.reconnect(f, v2, new_v)
    assert f.v1 != f.v2
    f.shift(new_v, len(v1.seq) + len(padding))

  # remove old vertices and edge
  g.remove_edge(e)
  g.remove_vertex_from_index(v1)
  g.remove_vertex_from_index(v2)

  assert len(new_v.seq) >= len(new_v)

  if DEBUG:
    for f in good_E:
      if f not in new_v.edges:
        print f.id, f.v1.id, f.v2.id
      if f in g.edges:
        assert f.v1 in g.vertices, f.v2 in g.vertices
      assert f in new_v.edges

    for w in good_V:
      for f in w.edges:
        if not (w == f.v1 or w == f.v2):
          print w.id, f.id, f.v2.id, f.v2.id
        assert w == f.v1 or w == f.v2

    for f in new_v.edges:
      if not (new_v == f.v1 or new_v == f.v2):
        print new_v.id, f.id, f.v2.id, f.v2.id
      assert new_v == f.v1 or new_v == f.v2

  return new_v

def contract_overlap_edge(g, e, E):
  raise NotImplementedError

# ----------------------------------------------------------------------------
# helpers

def _orient_th(g, e, v1, v2):
  """Changes edge and vertices so that e connects v1, v2 as T->H."""

  if e.connection[e.v1] == e.connection[e.v2]:
    if e.connection[e.v1] == 'H':
      g.flip_vertex(e.v1)
    elif e.connection[e.v1] == 'T':
      g.flip_vertex(e.v2)
  elif e.connection[e.v1] == 'H' and e.connection[e.v2] == 'T':
    e.flip()

def _merge_metadata(new_v, v1, v2, shift):
  # merge wells
  for w in v1.wells:
    s, e = v1.well_interval(w)
    new_v.add_well(w, s, e)
  for w in v2.wells:
    s, e = v2.well_interval(w)
    new_v.add_well(w, s+shift, e+shift)

  # merge intervals
  for ivl in v1.intervals:
    new_v.add_interval(ivl)
  for ivl in v2.intervals:
    new_v.add_interval(ivl)
