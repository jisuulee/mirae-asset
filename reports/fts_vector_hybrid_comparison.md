# FTS vs Exact Vector vs Hybrid

> Full-120 results are internal/candidate results.

## Category

| group | FTS Top-1/R@10 | Vector Top-1/R@10 | Hybrid Top-1/R@10 |
|---|---:|---:|---:|
| ambiguous_similar_sections | 0.0%/0.0% | 10.0%/10.0% | 10.0%/10.0% |
| company_concept | 13.3%/13.3% | 6.7%/33.3% | 13.3%/46.7% |
| company_section_title | 26.7%/33.3% | 0.0%/33.3% | 6.7%/53.3% |
| difficult_or_ambiguous | 0.0%/0.0% | 20.0%/20.0% | 20.0%/20.0% |
| exact_phrase | 40.0%/40.0% | 0.0%/20.0% | 10.0%/60.0% |
| narrative_description | 10.0%/10.0% | 0.0%/20.0% | 0.0%/30.0% |
| partial_korean_phrase | 10.0%/10.0% | 0.0%/30.0% | 10.0%/30.0% |
| pdf_fallback | 20.0%/20.0% | 60.0%/80.0% | 60.0%/100.0% |
| spacing_or_expression_variation | 10.0%/10.0% | 0.0%/30.0% | 0.0%/40.0% |
| table_related | 66.7%/66.7% | 0.0%/0.0% | 33.3%/66.7% |
| year_report_filtered | 10.0%/30.0% | 20.0%/80.0% | 20.0%/90.0% |

## Difficulty

| group | FTS Top-1/R@10 | Vector Top-1/R@10 | Hybrid Top-1/R@10 |
|---|---:|---:|---:|
| easy | 32.0%/36.0% | 0.0%/28.0% | 8.0%/56.0% |
| hard | 5.0%/5.0% | 25.0%/30.0% | 25.0%/35.0% |
| medium | 13.2%/16.2% | 4.4%/33.8% | 8.8%/45.6% |
