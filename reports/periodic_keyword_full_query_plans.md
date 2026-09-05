# Full corpus keyword query plans (after indexes)

Query loaded from benchmark; UTF-8; namespace=periodic-full-v1

## fts
```text
Limit  (cost=64.52..64.52 rows=1 width=12) (actual time=366.948..366.951 rows=10 loops=1)
  Buffers: shared hit=9584 read=6174
  ->  Sort  (cost=64.52..64.52 rows=1 width=12) (actual time=366.947..366.948 rows=10 loops=1)
        Sort Key: (ts_rank_cd(search_tsv, '''i'' & ''회사의'' & ''개요'''::tsquery)) DESC
        Sort Method: top-N heapsort  Memory: 25kB
        Buffers: shared hit=9584 read=6174
        ->  Bitmap Heap Scan on document_sections s  (cost=60.49..64.51 rows=1 width=12) (actual time=5.940..364.676 rows=6249 loops=1)
              Recheck Cond: (((metadata ->> 'loader_namespace'::text) = 'periodic-full-v1'::text) AND (search_tsv @@ '''i'' & ''회사의'' & ''개요'''::tsquery))
              Heap Blocks: exact=3298
              Buffers: shared hit=9581 read=6174
              ->  BitmapAnd  (cost=60.49..60.49 rows=1 width=0) (actual time=5.393..5.394 rows=0 loops=1)
                    Buffers: shared hit=6 read=91
                    ->  Bitmap Index Scan on document_sections_loader_namespace_idx  (cost=0.00..7.40 rows=414 width=0) (actual time=3.299..3.299 rows=82862 loops=1)
                          Index Cond: ((metadata ->> 'loader_namespace'::text) = 'periodic-full-v1'::text)
                          Buffers: shared read=73
                    ->  Bitmap Index Scan on document_sections_search_idx  (cost=0.00..52.84 rows=112 width=0) (actual time=1.326..1.326 rows=6249 loops=1)
                          Index Cond: (search_tsv @@ '''i'' & ''회사의'' & ''개요'''::tsquery)
                          Buffers: shared hit=6 read=18
Planning:
  Buffers: shared hit=203 read=58
Planning Time: 2.328 ms
Execution Time: 367.091 ms
```

## trigram
```text
Limit  (cost=358.91..358.94 rows=10 width=12) (actual time=16742.654..16742.658 rows=10 loops=1)
  Buffers: shared hit=108923 read=36132
  ->  Sort  (cost=358.91..358.94 rows=10 width=12) (actual time=16742.652..16742.654 rows=10 loops=1)
        Sort Key: (GREATEST(similarity(search_text_normalized, 'I. 회사의 개요'::text), similarity(search_text_nospace, 'I. 회사의 개요'::text))) DESC
        Sort Method: top-N heapsort  Memory: 25kB
        Buffers: shared hit=108923 read=36132
        ->  Bitmap Heap Scan on document_sections s  (cost=319.01..358.75 rows=10 width=12) (actual time=103.698..16741.395 rows=1031 loops=1)
              Recheck Cond: (((metadata ->> 'loader_namespace'::text) = 'periodic-full-v1'::text) AND ((search_text_normalized % 'I. 회사의 개요'::text) OR (search_text_nospace % 'I. 회사의 개요'::text)))
              Rows Removed by Index Recheck: 19109
              Heap Blocks: exact=9798
              Buffers: shared hit=108923 read=36132
              ->  BitmapAnd  (cost=319.01..319.01 rows=10 width=0) (actual time=35.708..35.710 rows=0 loops=1)
                    Buffers: shared hit=151 read=190
                    ->  Bitmap Index Scan on document_sections_loader_namespace_idx  (cost=0.00..7.40 rows=414 width=0) (actual time=3.073..3.074 rows=82862 loops=1)
                          Index Cond: ((metadata ->> 'loader_namespace'::text) = 'periodic-full-v1'::text)
                          Buffers: shared hit=73
                    ->  BitmapOr  (cost=311.36..311.36 rows=2047 width=0) (actual time=31.813..31.813 rows=0 loops=1)
                          Buffers: shared hit=78 read=190
                          ->  Bitmap Index Scan on document_sections_search_trgm_idx  (cost=0.00..155.68 rows=1024 width=0) (actual time=20.088..20.088 rows=20140 loops=1)
                                Index Cond: (search_text_normalized % 'I. 회사의 개요'::text)
                                Buffers: shared hit=51 read=125
                          ->  Bitmap Index Scan on document_sections_nospace_trgm_idx  (cost=0.00..155.68 rows=1024 width=0) (actual time=11.722..11.722 rows=11018 loops=1)
                                Index Cond: (search_text_nospace % 'I. 회사의 개요'::text)
                                Buffers: shared hit=27 read=65
Planning:
  Buffers: shared hit=21 read=9
Planning Time: 8.418 ms
Execution Time: 16752.897 ms
```

## combined
```text
Limit  (cost=415.81..415.83 rows=10 width=12) (actual time=17256.905..17256.909 rows=10 loops=1)
  Buffers: shared hit=177645 read=50075
  ->  Sort  (cost=415.81..415.83 rows=11 width=12) (actual time=17256.903..17256.906 rows=10 loops=1)
        Sort Key: ((ts_rank_cd(search_tsv, '''i'' & ''회사의'' & ''개요'''::tsquery) + GREATEST(similarity(search_text_normalized, 'I. 회사의 개요'::text), similarity(search_text_nospace, 'I. 회사의 개요'::text)))) DESC
        Sort Method: top-N heapsort  Memory: 25kB
        Buffers: shared hit=177645 read=50075
        ->  Bitmap Heap Scan on document_sections s  (cost=371.85..415.62 rows=11 width=12) (actual time=26.705..17250.745 rows=6249 loops=1)
              Recheck Cond: (((metadata ->> 'loader_namespace'::text) = 'periodic-full-v1'::text) AND ((search_tsv @@ '''i'' & ''회사의'' & ''개요'''::tsquery) OR (search_text_normalized % 'I. 회사의 개요'::text) OR (search_text_nospace % 'I. 회사의 개요'::text)))
              Rows Removed by Index Recheck: 13891
              Heap Blocks: exact=9798
              Buffers: shared hit=177645 read=50075
              ->  BitmapAnd  (cost=371.85..371.85 rows=11 width=0) (actual time=14.332..14.334 rows=0 loops=1)
                    Buffers: shared hit=90 read=275
                    ->  Bitmap Index Scan on document_sections_loader_namespace_idx  (cost=0.00..7.40 rows=414 width=0) (actual time=3.121..3.121 rows=82862 loops=1)
                          Index Cond: ((metadata ->> 'loader_namespace'::text) = 'periodic-full-v1'::text)
                          Buffers: shared read=73
                    ->  BitmapOr  (cost=364.20..364.20 rows=2159 width=0) (actual time=10.226..10.227 rows=0 loops=1)
                          Buffers: shared hit=90 read=202
                          ->  Bitmap Index Scan on document_sections_search_idx  (cost=0.00..52.84 rows=112 width=0) (actual time=1.128..1.128 rows=6249 loops=1)
                                Index Cond: (search_tsv @@ '''i'' & ''회사의'' & ''개요'''::tsquery)
                                Buffers: shared hit=6 read=18
                          ->  Bitmap Index Scan on document_sections_search_trgm_idx  (cost=0.00..155.68 rows=1024 width=0) (actual time=5.840..5.840 rows=20140 loops=1)
                                Index Cond: (search_text_normalized % 'I. 회사의 개요'::text)
                                Buffers: shared hit=54 read=122
                          ->  Bitmap Index Scan on document_sections_nospace_trgm_idx  (cost=0.00..155.68 rows=1024 width=0) (actual time=3.256..3.256 rows=11018 loops=1)
                                Index Cond: (search_text_nospace % 'I. 회사의 개요'::text)
                                Buffers: shared hit=30 read=62
Planning:
  Buffers: shared read=4
Planning Time: 7.351 ms
Execution Time: 17257.138 ms
```
