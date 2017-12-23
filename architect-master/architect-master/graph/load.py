import pickle
import pysam
import logging

import intervals
from string_graph import AssemblyVertex, OverlapEdge, ScaffoldEdge, \
                         AssemblyGraph, no_diedge

from common.util import reverse_complement

# ----------------------------------------------------------------------------
# constants

TSV_LEFT = 'L'
TSV_RIGHT = 'R'
TSV_SAME = 'S'
TSV_REVERSE = 'R'
TSV_TYPE_OVL = 'O'
TSV_TYPE_SCA = 'S'

CTMT_WELL_REC = 'W'
CTMT_IVL_REC = 'R'

DEBUG = False

# ----------------------------------------------------------------------------
# main functions

def load_from_fasta_tsv(fasta_path, tsv_path=None, containment_path=None, min_supp=3):
  g, vertices_by_contig = _load_from_fasta(fasta_path)
  if tsv_path:
    _load_edges_from_tsv(g, tsv_path, vertices_by_contig, min_supp)
    if DEBUG: _verify_edges(g)
  if containment_path:
    _load_containment(g, containment_path, vertices_by_contig)
  return g

def unpickle_graph(pickle_path):
  with open(pickle_path, 'rb') as f:
    g = pickle.load(f)
  return g

def save_to_fasta_tsv(g, fasta_path, tsv_path, containment_path):
  _write_fasta(g, fasta_path)
  _write_edge_tsv(g, tsv_path)
  _write_containment(g, containment_path)

def save_fasta(g, fasta_file):
  with open(fasta_file, 'w') as fasta:
    for v in g.vertices:
      fasta.write('>' + str(v.id) + '\n')
      fasta.write(v.seq + '\n')

def save_ordering(g, ordering_file):
  with open(ordering_file, 'w') as out:
    for v in g.vertices:
      out.write('%d\t' % v.id)
      if v.contigs:
        v_str = '\t'.join([_ctg_str(ctg) for ctg in v.contigs])
      else:
        v_str = ''
      out.write('%s\n' % v_str)

def save_gfa(g, gfa_file):
  """Save graph to GFA"""
  with open(gfa_file, 'w') as out:
    for v in g.vertices:
      vh, vt = v.id*2, v.id*2 + 1
      out.write('S\t%d\t%d\t%s\t*\n' % (vh, vt, v.seq))
    for e in g.edges:
      v1, v2 = e.v1, e.v2
      id1 = 2*v1.id if e.connection[v1] == 'H' else 2*v1.id+1
      id2 = 2*v2.id if e.connection[v2] == 'H' else 2*v2.id+1
      out.write('L\t%d\t%d\t3000N\n' % (id1, id2))

def save_bandage_gfa(g, gfa_file):
  """Save graph in GFA format that can be read by Bandage"""
  with open(gfa_file, 'w') as out:
    for v in g.vertices:
      vh, vt = '%s+' % v.id, '%s-' % v.id
      out.write('S\t%s\t%s\t%s\t*\n' % (vh, vt, v.seq))
    for e in g.edges:
      v1, v2 = e.v1, e.v2
      conn1 = '+' if e.connection[v1] == 'H' else '-'
      conn2 = '+' if e.connection[v2] == 'H' else '-'
      out.write('L\t%d\t%s\t%d\t%s\t3000N\n' % (v1.id, conn1, v2.id, conn2))

def pickle_graph(g, pickle_path):
  """Pickle the graph object"""
  with open(pickle_path, 'wb') as f:
    pickle.dump(g, f)

# ----------------------------------------------------------------------------
# loading the graph

def _load_from_fasta(fasta_path):
  g = AssemblyGraph()
  vertices_by_contig = dict()

  fasta = pysam.FastaFile(fasta_path)
  n = len(fasta.references)
  for i, ctg in enumerate(fasta.references):
    # if i % 1000 == 0: print '%d/%d' % (i, n)
    id_ = g.vertex_id_generator.get_id()
    seq = fasta.fetch(ctg).upper()
    v = AssemblyVertex(id_, seq)
    assert ctg not in vertices_by_contig
    vertices_by_contig[ctg] = v
    g.add_vertex(v)

  logging.info('Contigs loaded')

  return g, vertices_by_contig

def _load_edges_from_tsv(g, tsv_path, vertices_by_contig=None, min_supp=3):
  if not vertices_by_contig:
    vertices_by_contig = {v.id : v for v in g.vertices}

  with open(tsv_path) as tsv:
    for line in tsv:
      type_, ctg1, ctg2, c1, c2, o, spt, d = line.strip().split()

      v1 = vertices_by_contig[ctg1]
      v2 = vertices_by_contig[ctg2]

      if c1 == TSV_LEFT:
        conn1 = 'H'
      elif c1 == TSV_RIGHT:
        conn1 = 'T'
      else:
        raise ValueError('Invalid connection value in .tsv')

      if c2 == TSV_LEFT:
        conn2 = 'H'
      elif c2 == TSV_RIGHT:
        conn2 = 'T'
      else:
        raise ValueError('Invalid connection value in .tsv')

      if o == TSV_SAME:
        ori = 0
      elif o == TSV_REVERSE:
        ori = 1
      else:
        raise ValueError('Invalid orientation value in .tsv')

      if type_ == TSV_TYPE_SCA:
        # if edge already exists, add to count
        if v1 in v2.neighbors:
          e_prev = v1.edge_to_vertex(v2)
          if e_prev.connection[v1] == conn1 \
          and e_prev.connection[v2] == conn2:
            logging.warning('Dupplicate records indicating edge ' \
                            'between %d (%s), %d (%s); ' \
                            'summing counts.' % (v1.id, ctg1, v2.id, ctg2))
            e.support += int(spt)
            continue

        # otherwise, it's a new edge
        if int(spt) < min_supp:
          continue

        j = g.edge_id_generator.get_id()
        e = ScaffoldEdge(j, v1, v2, conn1, conn2, ori, max(int(d),0))
        e.support = int(spt)

      elif type_ == TSV_TYPE_OVL:
        #TODO: need to implement this
        raise ValueError('Parsing of overlap edges in TSV not implemented')

      else:
        raise ValueError('Invalid edge type found: %s' % type_)

      g.add_edge(e)

      for v in (v1, v2):
        if e.connection[v] == 'H':
          v.head_edges.add(e)
        elif e.connection[v] == 'T':
          v.tail_edges.add(e)
        else:
          raise Exception('ERROR: Invalid edge connection!')

  logging.info('Edge connections loaded')

  return g

def _load_containment(g, containment_file, vertices_by_contig=None):
  if not vertices_by_contig:
    vertices_by_contig = {v.id : v for v in g.vertices}

  with open(containment_file) as in_:
    for line in in_:
      fields = line.split()

      name = fields[1]
      v = vertices_by_contig.get(name, None)
      if not v:
        logging.warning('WARNING: Vertex not found:', name)
        continue
      
      if fields[0] == CTMT_WELL_REC:
        well, start, end = int(fields[2]), int(fields[3]), int(fields[4])
        v.add_well(well, start, end)

      elif fields[0] == CTMT_IVL_REC:
        ivl = (int(fields[2]), int(fields[3]), int(fields[4]))
        v.add_interval(ivl)

      else:
        logging.warning('WARNING: Invalid record type found:', fields[0])

# ----------------------------------------------------------------------------
# saving the graph

def _write_edge_tsv(g, tsv_file):
  with open(tsv_file, 'w') as tsv:
    for e in g.edges:
      if e.is_scaffold_edge:
        vid1, vid2 = e.v1.id, e.v2.id
        vc1 = TSV_LEFT if e.connection[e.v1] == 'H' else TSV_RIGHT
        vc2 = TSV_LEFT if e.connection[e.v2] == 'H' else TSV_RIGHT
        ori = TSV_SAME if e.orientation == 1 else TSV_REVERSE
        spt, dis = e.support, e.distance
        tsv.write('%s\t%d\t%d\t%s\t%s\t%s\t%d\t%d\n' % 
          (TSV_TYPE_SCA, vid1, vid2, vc1, vc2, ori, spt, dis))

def _write_fasta(g, fasta_file):
  with open(fasta_file, 'w') as fasta:
    for v in g.vertices:
      fasta.write('>%d\n%s\n' % (v.id, v.seq))

def _write_containment(g, containment_file):
  with open(containment_file, 'w') as out:
    for v in g.vertices:
      for w in v.wells:
        s, e = v.well_interval(w)
        out.write('%s\t%d\t%d\t%d\t%d\n' % (CTMT_WELL_REC, v.id, w, s, e))
      for ivl in v.intervals:
        out.write('%s\t%d\t%d\t%d\t%d\n' % (CTMT_IVL_REC, v.id, ivl[0], ivl[1], ivl[2]))

def _ctg_str(ctg): 
  id_, ivls, length, strand = ctg
  ivl_str = ','.join(intervals.parse_intervals(ivls))
  return '%d;%s;%d;%s' % (id_, ivl_str, length, strand)

# ----------------------------------------------------------------------------
# helpers

def _verify_edges(g):
  for e in g.edges:
    assert e in e.v1.edges and e in e.v2.edges
