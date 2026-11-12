# Proposed evaluation questions (review draft)

These are candidate questions only. `eval/questions.yaml` and
`eval/human_grades.csv` remain empty for Aloo's final selection and grading.
The kickoff refers to five types in a playbook §7, but that playbook is not in
the project folder. The five provisional buckets below are direct fact,
numeric/method, comparison/synthesis, multi-hop, and unanswerable. Pages are
the papers' printed PDF pages; each chunk ID points to the exact extracted R1
chunk used to verify the excerpt.

## Direct fact

1. **What architecture did the authors propose, and which common operations does it dispense with?**
   - Evidence: `1706.03762v7`, p. 1, chunk `ecf34729c3ec1951` — “We propose a new simple network architecture, the Transformer, based solely on attention mechanisms, dispensing with recurrence and convolutions entirely.”
2. **How many identical layers are in the Transformer encoder stack?**
   - Evidence: `1706.03762v7`, p. 3, chunk `b494fd73f0534ace` — “The encoder is composed of a stack of N = 6 identical layers.”
3. **What additional sub-layer does each Transformer decoder layer have?**
   - Evidence: `1706.03762v7`, p. 3, chunk `fde064ddef2ed795` — “the decoder inserts a third sub-layer, which performs multi-head attention over the output of the encoder stack.”
4. **What three main components make up Moshi's architecture?**
   - Evidence: `2410.00037v1`, p. 6, chunk `0cbfae0d4019333a` — “a bespoke text language model backbone (Helium, see Section 3.2)”; “a neural audio codec with residual vector quantization” (Mimi); and “the streaming, hierarchical generation of semantic and acoustic tokens.”
5. **Which spoken-conversation behaviors do the authors say turn-based models miss?**
   - Evidence: `2410.00037v1`, p. 2, chunk `8ec62039552cbec3` — “it falls short in modeling aspects of spoken conversations such as interruptions, overlapping speech— which amounts for 10 to 20% of spoken time”
6. **What modules make up H-Net's chunking layer?**
   - Evidence: `2507.07955v2`, p. 6, chunk `c9034be23de061a1` — “The chunking layer (Chunk in equation (2)) contains a routing module and downsampler,”
7. **What modules make up H-Net's dechunking layer?**
   - Evidence: `2507.07955v2`, p. 6, chunk `a70741935f0c7653` — “The dechunking layer (Dechunk in equation (3)) consists of a smoothing module and upsampler,”
8. **What signal does H-Net's routing module use to identify possible boundaries?**
   - Evidence: `2507.07955v2`, p. 6, chunk `c9034be23de061a1` — “The routing module implements this intuition through cosine similarity between adjacent encoder outputs.”

## Numeric and method

9. **What BLEU score did the big Transformer report on WMT 2014 English-to-German?**
   - Evidence: `1706.03762v7`, p. 8, chunk `f8c3504b4b3841e7` — “establishing a new state-of-the-art BLEU score of 28.4.”
10. **What hardware and training duration did the paper report for the base Transformer?**
    - Evidence: `1706.03762v7`, p. 7, chunk `a74289279fd72be7` — “one machine with 8 NVIDIA P100 GPUs”; base models were trained for “100,000 steps or 12 hours.”
11. **What target compression ratio did the one-stage H-Net language-model experiment use?**
   - Evidence: `2507.07955v2`, p. 11, chunk `ab898751a220edeb` — “with compression target 𝑁0 = 6.”
12. **Approximately how many bytes per chunk did learned dynamic chunking produce?**
    - Evidence: `2507.07955v2`, p. 3, chunk `2f990d962e0400af` — “a similar resolution as BPE tokenizers (4.5-5 bytes/chunk)”
13. **At what training-data sizes did the H-Net variants cross the tokenized Transformer in the XL comparison?**
    - Evidence: `2507.07955v2`, p. 12, chunk `9c6bbf025fbc1469` — “cross over the tokenized Transformer after just 200B bytes, 100B bytes, and 30B bytes respectively.”
14. **How much did two-stage H-Net exceed the Transformer baseline in average downstream performance at Large and XL scales?**
   - Evidence: `2507.07955v2`, p. 12, chunk `306bd8dedc31e6ce` — “H-Net (2-stage) consistently achieves the highest performance across most tasks, outperforming 2.2% and 2.6% over the Transformer baseline at Large and XL scales respectively.”
15. **How many Mimi RVQ quantizers and codebook entries produced the reported 1.1 kbps bitrate at 12.5 Hz?**
    - Evidence: `2410.00037v1`, p. 11, chunk `d2c4e656db5a45b8` — “use Q = 8 quantizers, each with a codebook size of NA = 2048. At 12.5Hz, this represents a bitrate of 1.1kbps.”
16. **What initial frame duration does Mimi need to emit its first latent timestep?**
    - Evidence: `2410.00037v1`, p. 10, chunk `d69a6602c7af1346` — “given a first audio frame of 80ms, Mimi outputs a first latent timestep,”
17. **What WER did Moshi report for streaming TTS and its ASR system?**
    - Evidence: `2410.00037v1`, p. 31, chunks `141df68db1ca529e` and `165489a52d333f89` — streaming TTS: “4.7% of WER”; ASR: “5.7% WER.”
18. **How much theoretical latency did Moshi report, and what natural-conversation average did it compare against?**
    - Evidence: `2410.00037v1`, p. 2, chunk `3f49a97d8eadaf65` — “latency of 160 ms—lower than the 230 ms average in natural conversations measured over 10 languages”

## Comparison and synthesis

19. **Why do the authors say scaled dot-product attention is faster and more space-efficient than additive attention in practice?**
   - Evidence: `1706.03762v7`, p. 4, chunk `e8118712b3430e7a` — “much faster and more space-efficient in practice, since it can be implemented using highly optimized matrix multiplication code.”
20. **How do self-attention and recurrent layers differ in sequential operations and maximum path length?**
    - Evidence: `1706.03762v7`, p. 6, chunk `a1cfeed2213f0485` — table reports self-attention `O(1)` sequential operations and `O(1)` maximum path length, versus recurrent `O(n)` and `O(n)`.
21. **What does the paper say multi-head attention gains over a single attention head?**
    - Evidence: `1706.03762v7`, p. 5, chunk `52a700bc9b174cbf` — “Multi-head attention allows the model to jointly attend to information from different representation subspaces at different positions. With a single attention head, averaging inhibits this.”
22. **Which limitation of dGSLM does Moshi address, and how?**
    - Evidence: `2410.00037v1`, p. 6, chunk `94e9779702c36b47` — dGSLM “does not run in an online fashion,” lacks text-LLM knowledge, and models only semantic tokens; Moshi models semantic and acoustic streams hierarchically.
23. **How did Moshi's streaming TTS WER compare with Vall-E and NaturalSpeech 3, and what lookahead did Moshi need?**
    - Evidence: `2410.00037v1`, p. 31, chunk `141df68db1ca529e` — “4.7% of WER”; Vall-E “5.9%”; NaturalSpeech 3 “1.81%”; “Moshi only requires 2 seconds of lookahead.”
24. **What does the H-Net pooling ablation imply about fixed-width chunking?**
   - Evidence: `2507.07955v2`, p. 12, chunk `49ca30009569fb04` — “H-Net (pool) is much worse than all other H-Net variants, validating that fixed-width chunking is not effective.”
25. **How did SpaceByte++ and the H-Net variants compare with the BPE Transformer at Large and XL scales?**
    - Evidence: `2507.07955v2`, p. 12, chunk `306bd8dedc31e6ce` — SpaceByte++, H-Net (space), and H-Net (1-stage) had performance “similar ... at Large scale” and “slightly outperform it at the XL scale”; H-Net (2-stage) “consistently achieves the highest performance across most tasks.”
26. **How does the Transformer big model compare to earlier reported models on the WMT 2014 English-to-French task?**
    - Evidence: `1706.03762v7`, p. 8, chunk `f8c3504b4b3841e7` — it reports BLEU 41.0 and “less than 1/4 the training cost of the previous state-of-the-art model.”

## Multi-hop and mechanism

27. **Why does the Transformer add positional encodings, and what functions does this paper use?**
    - Evidence: `1706.03762v7`, p. 6, chunk `9f6ad744799d4278` — because it has “no recurrence and no convolution,” it adds positional information; “we use sine and cosine functions of different frequencies.”
28. **How does scaled dot-product attention turn a query and keys into weights on values?**
    - Evidence: `1706.03762v7`, p. 4, chunk `77a0c9b803b98870` — it computes query-key dot products, divides by `sqrt(dk)`, then applies softmax “to obtain the weights on the values.”
29. **How does the Transformer decoder prevent a position from using future output positions?**
    - Evidence: `1706.03762v7`, p. 5, chunk `4020c8d8c71aeec3` — it preserves autoregression by masking illegal connections, setting their softmax inputs to `−∞`.
30. **How do Helium, Mimi, and Moshi's token streams work together to support full-duplex dialogue?**
    - Evidence: `2410.00037v1`, p. 6, chunks `0cbfae0d4019333a` and `94e9779702c36b47` — architecture combines a text LLM, semantic audio codec, and hierarchical semantic/acoustic streams; the paper says this lets Moshi operate full duplex and benefit from Helium's language knowledge.
31. **How does Inner Monologue use text during generation, and what quality effect do the authors report?**
    - Evidence: `2410.00037v1`, p. 15, chunk `f449e374300a8557` — text acts “as a prefix to the generation of semantic tokens”; the authors say it provides “a scaffolding that increases the linguistic quality.”
32. **How do the delayed streams and teacher-forced inputs differ between Moshi's streaming TTS and ASR?**
    - Evidence: `2410.00037v1`, p. 31, chunk `b018814c525086ac` — TTS delays audio tokens by 2s and teacher-forces text; ASR delays text tokens by 2s and teacher-forces audio.
33. **How do H-Net's routing decisions determine which vectors remain in the compressed sequence?**
   - Evidence: `2507.07955v2`, p. 6, chunks `c9034be23de061a1` and `0b2eb98f67af1e72` — routing uses adjacent-output cosine similarity; vectors with `bt = 1` are retained, and those with `bt = 0` are discarded.
34. **Trace an H-Net stage from input encoding through compression to output reconstruction.**
   - Evidence: `2507.07955v2`, p. 5, chunk `963dc2311051d27f` — it compresses through a chunking layer, processes fewer richer vectors in the main network, then restores resolution through a dechunking layer.

## Unanswerable (refusal candidates)

35. **How many kilograms of CO2 did the Transformer training run emit?**
    - Nearest evidence: `1706.03762v7`, p. 7, chunk `a74289279fd72be7` reports hardware and elapsed training time, but no energy or emissions measurement. The answer must not infer emissions from GPU count and duration.
36. **What dollar amount did the Transformer authors pay for the training run?**
    - Nearest evidence: `1706.03762v7`, p. 8, chunk `60742ef7abf3ea88` compares training cost in FLOPs; it does not give a monetary bill.
37. **What was the Transformer big model's inference latency on a specified GPU?**
    - Nearest evidence: `1706.03762v7`, p. 7, chunk `a74289279fd72be7` gives training hardware/time, not inference latency or an inference GPU configuration.
38. **Which languages does Moshi officially support, and how many are supported?**
    - Nearest evidence: `2410.00037v1`, p. 3, chunk `9dbc4fd644b48039` says Helium was pretrained on public English data; p. 2's reference to 10 languages describes a cited natural-conversation latency study, not Moshi's supported-language list.
39. **What exact GPU model and VRAM did the authors use to serve Moshi?**
    - Nearest evidence: `2410.00037v1`, p. 42, chunk `9c544074eff7c97c` thanks Hugging Face for inference compute donation but does not identify GPU model or VRAM. The paper says models were trained on H100 GPUs (p. 20, chunk `086965018fe9e62d`); that is training hardware, not evidence of the serving hardware.
40. **What exact GPU model did the H-Net authors use for XL training, and how many hours did that run take?**
    - Nearest evidence: `2507.07955v2`, p. 11, chunk `c1308d2926dc2036` discusses FLOP matching but does not specify an XL-run GPU model or wall-clock duration.

## Demonstrated naive-BM25 failure candidates

BM25 top-four results below were reproduced locally from all R1 chunks with the current `PaperRetriever` and `top_k=4`. These are retrieval-failure candidates, not proof that a full answer pipeline will fail; keep only the ones Aloo agrees are misleading.

1. **Question:** “What GPU was used for inference in Moshi?”
   - Top four: (1) `1706.03762v7` p. 8 `a78b0aca8137b889`, a Transformer GPU/table note; (2) `2410.00037v1` p. 31 `f47b858bc3507135`, Moshi quantization; (3) p. 42 `9c544074eff7c97c`, Moshi acknowledgements; (4) p. 62 `d8f842f42d9fd517`, synthetic-transcript appendix.
   - Failure mode: the word *GPU* pulls in an unrelated paper's table; a generator could mistake that hardware context for Moshi's serving setup.
2. **Question:** “What BLEU score did Moshi achieve on English-to-German machine translation?”
   - Top four: `1706.03762v7` p. 8 `f8c3504b4b3841e7` (Transformer's English-to-German BLEU result), p. 1 `ecf34729c3ec1951` (abstract), p. 8 `60742ef7abf3ea88` (results table), and p. 10 `dcdd8c649c57f4c6` (conclusion). None is Moshi.
   - Failure mode: the shared BLEU/translation terms retrieve a plausible numeric result from the wrong paper.
3. **Question:** “What was the response latency of the Transformer model?”
   - Local variant tested: “How many milliseconds is the Transformer response time?” Top four are all Moshi chunks: p. 2 `51b3062aa2b2be98`, p. 37 `c486e14dc3f43a72`, p. 6 `0d6038024a692896`, and p. 38 `4c2dccaad4ef1f29`. The top hit mentions Moshi's 160 ms theoretical spoken-dialogue latency, which is not Transformer inference latency.
   - Failure mode: a model-name mismatch plus latency vocabulary can pull a precise but irrelevant number from another paper.

Two additional hypotheses were tested but are not promoted as failures: H-Net “compression ratio” retrieves several distinct stage-ratio plots (the question itself needs a stage qualifier); “languages Moshi supports” retrieves synthetic dialogue examples rather than an explicit support inventory. Both need better question wording or more evidence before being counted as a robust failure.
