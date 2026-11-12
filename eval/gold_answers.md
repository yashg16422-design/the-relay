# The Relay: 35-question gold answer key

Accepted by Aloo on 2026-11-11 ("i accept the gold_answers"). The answer text and evidence below were not rewritten at acceptance. Questions are source-grounded in the exact versions listed in `library/MANIFEST.json`. PDF pages are 1-based viewer page numbers. Type labels were assigned without the missing playbook §7 and stay as written. Saved E5 answers were graded on 2026-11-11 into `eval/human_grades.csv` (134 successful answers; HTTP 429 attempts are not graded). Those 134 grades were then copied into the matching `results/results.csv` `answer_grade` cells. The answer text below is unchanged.

## Distribution

- 7 direct facts
- 7 numeric/method
- 7 comparison/synthesis
- 7 multi-hop/mechanism
- 7 unanswerable/refusal

## Answer key

### Q01 - direct fact
**Question:** What architecture did the authors propose, and which common operations does it dispense with?
**Gold answer:** The Transformer is an architecture based solely on attention; it dispenses with recurrence and convolutions.
**Evidence:**
- `1706.03762v7`, PDF p. 1, §Abstract, R1 chunk `ecf34729c3ec1951`: “We propose a new simple network architecture, the Transformer, based solely on attention mechanisms, dispensing with recurrence and convolutions entirely.”

### Q02 - direct fact
**Question:** How many identical layers are in the Transformer encoder stack?
**Gold answer:** Six identical layers.
**Evidence:**
- `1706.03762v7`, PDF p. 3, §3.1 Encoder and Decoder Stacks, R1 chunk `b494fd73f0534ace`: “The encoder is composed of a stack of N = 6 identical layers.”

### Q03 - direct fact
**Question:** What additional sub-layer does each Transformer decoder layer have beyond the encoder's two sub-layers?
**Gold answer:** A third sub-layer performs multi-head attention over the encoder stack output.
**Evidence:**
- `1706.03762v7`, PDF p. 3, §3.1 Encoder and Decoder Stacks, R1 chunk `b494fd73f0534ace`: “the decoder inserts a third sub-layer, which performs multi-head attention over the output of the encoder stack.”

### Q04 - direct fact
**Question:** What three main components make up Moshi's architecture?
**Gold answer:** The Helium text language model backbone; Mimi, a neural audio codec with residual vector quantization and distilled semantic knowledge; and streaming hierarchical generation of semantic and acoustic tokens.
**Evidence:**
- `2410.00037v1`, PDF p. 6, §Front matter, R1 chunk `0cbfae0d4019333a`: “The main components of Moshi’s architecture are: a bespoke text language model backbone (Helium, see Section 3.2); a neural audio codec with residual vector quantization and with semantic knowledge distilled from a self-supervised speech model (Mimi, Section 3.3); the streaming, hierarchical generation of semantic and acoustic tokens for both the user and Moshi, along with time-aligned text tokens for Moshi when using Inner Monologue (Section 3.4).”

### Q05 - direct fact
**Question:** Which spoken-conversation behaviors do the authors say turn-based models miss?
**Gold answer:** Interruptions, overlapping speech, and backchanneling; the paper says interruptions and overlap account for 10–20% of spoken time.
**Evidence:**
- `2410.00037v1`, PDF p. 2, §1 Introduction, R1 chunk `8ec62039552cbec3`: “it falls short in modeling aspects of spoken conversations such as interruptions, overlapping speech— which amounts for 10 to 20% of spoken time (C¸etin and Shriberg, 2006) —and backchanneling (i.e. non-interrupting interjections such as “OK” or “I see”).”

### Q06 - direct fact
**Question:** What modules make up H-Net's chunking layer?
**Gold answer:** A routing module and a downsampler.
**Evidence:**
- `2507.07955v2`, PDF p. 6, §2.2.1 Chunking Layer, R1 chunk `c9034be23de061a1`: “The chunking layer (Chunk in equation (2)) contains a routing module and downsampler,”

### Q07 - direct fact
**Question:** What modules make up H-Net's dechunking layer?
**Gold answer:** A smoothing module and an upsampler.
**Evidence:**
- `2507.07955v2`, PDF p. 6, §2.2.2 Dechunking, R1 chunk `a70741935f0c7653`: “The dechunking layer (Dechunk in equation (3)) consists of a smoothing module and upsampler,”

### Q08 - numeric method
**Question:** What BLEU score did the big Transformer report on WMT 2014 English-to-German?
**Gold answer:** 28.4 BLEU.
**Evidence:**
- `1706.03762v7`, PDF p. 8, §6.1 Machine Translation, R1 chunk `f8c3504b4b3841e7`: “establishing a new state-of-the-art BLEU score of 28.4.”

### Q09 - numeric method
**Question:** What hardware and training duration did the paper report for the base Transformer?
**Gold answer:** One machine with eight NVIDIA P100 GPUs; 100,000 steps, reported as 12 hours.
**Evidence:**
- `1706.03762v7`, PDF p. 7, §5.2 Hardware and Schedule, R1 chunk `a74289279fd72be7`: “We trained our models on one machine with 8 NVIDIA P100 GPUs. For our base models using the hyperparameters described throughout the paper, each training step took about 0.4 seconds. We trained the base models for a total of 100,000 steps or 12 hours.”

### Q10 - numeric method
**Question:** What compression target N₀ did the one-stage H-Net language-model experiment use?
**Gold answer:** N₀ = 6.
**Evidence:**
- `2507.07955v2`, PDF p. 11, §1024 , 1536 , 2048, R1 chunk `ab898751a220edeb`: “H-Net (1-stage) is our full H-Net method with DC learned end-to-end (Section 2.2) with compression target 𝑁0 = 6.”

### Q11 - numeric method
**Question:** Approximately how many bytes per chunk did the paper report for learned dynamic chunking?
**Gold answer:** About 4.5–5 bytes per chunk.
**Evidence:**
- `2507.07955v2`, PDF p. 3, §1 −'!"$, R1 chunk `2f990d962e0400af`: “a similar resolution as BPE tokenizers (4.5-5 bytes/chunk)”

### Q12 - numeric method
**Question:** At what training-data sizes did H-Net (space), H-Net (1-stage), and H-Net (2-stage) cross over the tokenized Transformer in the XL comparison?
**Gold answer:** After 200B, 100B, and 30B bytes, respectively.
**Evidence:**
- `2507.07955v2`, PDF p. 12, §3.1 Language Modeling, R1 chunk `9c6bbf025fbc1469`: “H-Net (space), H-Net (1-stage), and H-Net (2-stage) cross over the tokenized Transformer after just 200B bytes, 100B bytes, and 30B bytes respectively.”

### Q13 - numeric method
**Question:** How many Mimi RVQ quantizers and codebook entries per quantizer correspond to the reported 1.1 kbps bitrate at 12.5 Hz?
**Gold answer:** Eight quantizers, each with a codebook size of 2,048 entries.
**Evidence:**
- `2410.00037v1`, PDF p. 11, §Front matter, R1 chunk `d2c4e656db5a45b8`: “use Q = 8 quantizers, each with a codebook size of NA = 2048. At 12.5Hz, this represents a bitrate of 1.1kbps.”

### Q14 - numeric method
**Question:** What theoretical latency did Moshi report, and what natural-conversation average did it compare against?
**Gold answer:** 160 ms theoretical latency, compared with a 230 ms average measured over 10 languages.
**Evidence:**
- `2410.00037v1`, PDF p. 2, §1 Introduction, R1 chunk `3f49a97d8eadaf65`: “160 ms—lower than the 230 ms average in natural conversations measured over 10 languages (Stivers et al., 2009).”

### Q15 - comparison synthesis
**Question:** Why do the Transformer authors say scaled dot-product attention is faster and more space-efficient than additive attention in practice?
**Gold answer:** It can be implemented using highly optimized matrix multiplication code.
**Evidence:**
- `1706.03762v7`, PDF p. 4, §3.2.1 Scaled Dot-Product Attention, R1 chunk `e8118712b3430e7a`: “dot-product attention is much faster and more space-efficient in practice, since it can be implemented using highly optimized matrix multiplication code.”

### Q16 - comparison synthesis
**Question:** How do self-attention and recurrent layers compare in sequential operations and maximum path length, according to Table 1?
**Gold answer:** Self-attention has O(1) sequential operations and O(1) maximum path length; recurrent layers have O(n) for both. This is the table’s comparison, not a universal wall-clock guarantee.
**Evidence:**
- `1706.03762v7`, PDF p. 6, §Table 1: Maximum path lengths, per-layer complexity and minimum number of sequential operations, R1 chunk `a1cfeed2213f0485`: “LayerType | ComplexityperLayer | Sequential
Operations | MaximumPathLength
Self -Attention
Recurrent
Convolutional
Self -Attention (restricted) | O( n2 ·d)
O( n·d2)
O(k ·n·d2)
O( r·n·d) | O(1)
O( n)
O(1)
O(1) | O(1)
O( n)
O(logk( n))
O( n/ r)”

### Q17 - comparison synthesis
**Question:** What limitation of dGSLM does Moshi address, and what design choice does it use to address it?
**Gold answer:** The paper says dGSLM is not online, does not benefit from a text LM, and models semantic tokens without acoustic information. Moshi uses hierarchical semantic and acoustic streams, enabling full-duplex dialogue while using the text LM knowledge.
**Evidence:**
- `2410.00037v1`, PDF p. 6, §Front matter, R1 chunk `94e9779702c36b47`: “While dGSLM is full-duplex, it remains a proof-of-concept: it does not run in an online fashion, it does not benefit from the knowledge of a text language model, and it does not model acoustic information as it only models semantic tokens. Moshi addresses these limitations altogether: by modeling two streams of semantic and acoustic tokens hierarchically, Moshi is full duplex and can exploit all the information from the user (linguistic and non-linguistic) while producing speech in real time.”

### Q18 - comparison synthesis
**Question:** How did Moshi's streaming TTS WER compare with Vall-E and NaturalSpeech 3, and what lookahead did Moshi need?
**Gold answer:** Moshi reported 4.7% WER, better than Vall-E at 5.9% but worse than NaturalSpeech 3 at 1.81%; Moshi used 2 seconds of lookahead.
**Evidence:**
- `2410.00037v1`, PDF p. 31, §5.7 Streaming ASR and TTS, R1 chunk `141df68db1ca529e`: “Our streaming TTS model obtains 4.7% of WER on LibriSpeech test-clean, which outperforms Vall-E’s 5.9% WER but is worse than NaturalSpeech 3 (Ju et al., 2024) with 1.81%. Yet, Moshi only requires 2 seconds of lookahead”

### Q19 - comparison synthesis
**Question:** What does the H-Net pooling ablation suggest about fixed-width chunking?
**Gold answer:** In the reported experiment, the pooled H-Net baseline was much worse than other H-Net variants, which the authors interpret as evidence fixed-width chunking is ineffective.
**Evidence:**
- `2507.07955v2`, PDF p. 12, §3.1 Language Modeling, R1 chunk `49ca30009569fb04`: “H-Net (pool) is much worse than all other H-Net variants, validating that fixed-width chunking is not effective.”

### Q20 - comparison synthesis
**Question:** At Large and XL scales, how did SpaceByte++ and H-Net variants compare with the BPE Transformer on average downstream performance?
**Gold answer:** SpaceByte++, H-Net (space), and H-Net (1-stage) were similar at Large and slightly better at XL; H-Net (2-stage) had the highest performance across most tasks, exceeding the Transformer baseline by 2.2% at Large and 2.6% at XL.
**Evidence:**
- `2507.07955v2`, PDF p. 12, §3.1 Language Modeling, R1 chunk `306bd8dedc31e6ce`: “SpaceByte++, H-Net (space), and H-Net (1-stage) all have similar performance to the BPE Transformer at Large scale, and slightly outperform it at the XL scale, consistent with their close training curves (and possibly reflecting some noise in the evaluations). H-Net (2-stage) consistently achieves the highest performance across most tasks, outperforming 2.2% and 2.6% over the Transformer baseline at Large and XL scales respectively.”

### Q21 - comparison synthesis
**Question:** How did the Transformer big model compare with earlier reported models on WMT 2014 English-to-French?
**Gold answer:** It achieved 41.0 BLEU, outperforming previously published single models at less than one-quarter the training FLOPs of the previous state-of-the-art model.
**Evidence:**
- `1706.03762v7`, PDF p. 8, §6.1 Machine Translation, R1 chunk `f8c3504b4b3841e7`: “On the WMT 2014 English-to-French translation task, our big model achieves a BLEU score of 41.0, outperforming all of the previously published single models, at less than 1/4 the training cost of the previous state-of-the-art model.”

### Q22 - multi hop
**Question:** Why does the Transformer add positional encodings, and what functions does this paper use?
**Gold answer:** Because the model has no recurrence or convolution to convey sequence order, it injects positional information by adding encodings; it uses sine and cosine functions at different frequencies.
**Evidence:**
- `1706.03762v7`, PDF p. 6, §3.5 Positional Encoding, R1 chunk `9f6ad744799d4278`: “Since our model contains no recurrence and no convolution, in order for the model to make use of the order of the sequence, we must inject some information about the relative or absolute position of the tokens in the sequence.”
- `1706.03762v7`, PDF p. 6, §3.5 Positional Encoding, R1 chunk `9f6ad744799d4278`: “In this work, we use sine and cosine functions of different frequencies:”

### Q23 - multi hop
**Question:** How does scaled dot-product attention turn a query and keys into weights on values?
**Gold answer:** It computes each query-key dot product, divides the scores by √dₖ, applies softmax, and uses the resulting weights over the values.
**Evidence:**
- `1706.03762v7`, PDF p. 4, §3.2.1 Scaled Dot-Product Attention, R1 chunk `77a0c9b803b98870`: “We compute the dot products of the query with all keys, divide each by √dk, and apply a softmax function to obtain the weights on the values.”

### Q24 - multi hop
**Question:** How does the Transformer decoder prevent a position from using future output positions?
**Gold answer:** It masks illegal connections in scaled dot-product attention by setting the corresponding softmax inputs to −∞, preserving autoregressive information flow.
**Evidence:**
- `1706.03762v7`, PDF p. 5, §3.2.3 Applications of Attention in our Model, R1 chunk `4020c8d8c71aeec3`: “We need to prevent leftward information flow in the decoder to preserve the auto-regressive property. We implement this inside of scaled dot-product attention by masking out (setting to −∞) all values in the input of the softmax which correspond to illegal connections.”

### Q25 - multi hop
**Question:** How do Helium, Mimi, and Moshi's token streams work together to support full-duplex dialogue?
**Gold answer:** Helium supplies a text-LM backbone, Mimi encodes audio, and Moshi models user/system semantic and acoustic streams hierarchically; the paper says this supports full-duplex dialogue and lets the system benefit from Helium’s language knowledge.
**Evidence:**
- `2410.00037v1`, PDF p. 6, §Front matter, R1 chunk `0cbfae0d4019333a`: “a bespoke text language model backbone (Helium, see Section 3.2); a neural audio codec with residual vector quantization and with semantic knowledge distilled from a self-supervised speech model (Mimi, Section 3.3); the streaming, hierarchical generation of semantic and acoustic tokens for both the user and Moshi”
- `2410.00037v1`, PDF p. 6, §Front matter, R1 chunk `94e9779702c36b47`: “Moshi addresses these limitations altogether: by modeling two streams of semantic and acoustic tokens hierarchically, Moshi is full duplex and can exploit all the information from the user (linguistic and non-linguistic) while producing speech in real time.”

### Q26 - multi hop
**Question:** How does Inner Monologue use text during generation, and what effect do the authors report?
**Gold answer:** Moshi uses the text stream as a prefix to semantic-token generation; the authors say it provides scaffolding that increases linguistic quality.
**Evidence:**
- `2410.00037v1`, PDF p. 15, §3.4.4 Inner Monologue, R1 chunk `f449e374300a8557`: “having Moshi also model the textual representation of its own speech is providing a scaffolding that increases the linguistic quality of its generation.”
- `2410.00037v1`, PDF p. 15, §3.4.4 Inner Monologue, R1 chunk `f449e374300a8557`: “it acts as a prefix to the generation of semantic tokens.”

### Q27 - multi hop
**Question:** How do the delayed streams and teacher-forced inputs differ between Moshi's streaming TTS and ASR?
**Gold answer:** For TTS, audio tokens are delayed by 2 seconds and text tokens are teacher-forced at inference. For ASR, text tokens are delayed by 2 seconds and audio tokens are teacher-forced.
**Evidence:**
- `2410.00037v1`, PDF p. 31, §5.7 Streaming ASR and TTS, R1 chunk `b018814c525086ac`: “we train a streaming TTS model by delaying audio tokens by 2 seconds, giving some lookahead to the text tokens, and teacher forcing text tokens at inference. Similarly, we train a streaming ASR model by delaying text tokens by 2 seconds, allowing the model to listen to audio content before generating text tokens. In that case, at inference we teacher force the audio tokens.”

### Q28 - multi hop
**Question:** How does H-Net’s routing module turn adjacent-vector similarity into a compressed sequence?
**Gold answer:** It derives boundary probabilities from cosine similarity of adjacent projected encoder outputs, marks boundaries at the threshold, then retains vectors with bₜ = 1 and discards those with bₜ = 0.
**Evidence:**
- `2507.07955v2`, PDF p. 6, §2.2.1 Chunking Layer, R1 chunk `c9034be23de061a1`: “The routing module implements this intuition through cosine similarity between adjacent encoder outputs.”
- `2507.07955v2`, PDF p. 6, §2.2.1 Chunking Layer, R1 chunk `0b2eb98f67af1e72`: “vectors where 𝑏𝑡= 1 are retained
in the compressed sequence 𝑥𝑠+1, while those where 𝑏𝑡= 0 are discarded.”

### Q29 - unanswerable
**Question:** How many kilograms of CO₂ did the Transformer training run emit?
**Gold answer:** The paper does not report a CO₂-emissions figure, so it cannot be calculated from the reported GPU count and duration alone.
**Expected refusal:** State that the paper does not provide the emissions measurement; do not estimate from hardware/time.
**Nearest relevant evidence (not a gold answer):** 1706.03762v7, PDF p. 7, section 5.2 Hardware and Schedule, R1 chunk a74289279fd72be7: We trained our models on one machine with 8 NVIDIA P100 GPUs.

### Q30 - unanswerable
**Question:** What dollar amount did the Transformer authors pay for the training run?
**Gold answer:** The paper compares training compute in FLOPs but does not state an actual monetary bill.
**Expected refusal:** Say the dollar amount is not provided; do not convert FLOPs to a price.
**Nearest relevant evidence (not a gold answer):** 1706.03762v7, PDF p. 8, section Front matter, R1 chunk 60742ef7abf3ea88: Training Cost (FLOPs)

### Q31 - unanswerable
**Question:** What was the Transformer big model's inference latency on a specified GPU?
**Gold answer:** The paper reports training hardware and schedule, but no inference latency tied to a specified GPU.
**Expected refusal:** Say that the requested inference latency and GPU configuration are not stated; do not substitute training hardware/time.
**Nearest relevant evidence (not a gold answer):** 1706.03762v7, PDF p. 7, section 5.2 Hardware and Schedule, R1 chunk a74289279fd72be7: We trained our models on one machine with 8 NVIDIA P100 GPUs.

### Q32 - unanswerable
**Question:** Which languages does Moshi officially support, and how many are supported?
**Gold answer:** The paper describes English pretraining data for Helium but does not provide an official Moshi language-support list or count.
**Expected refusal:** Say the paper does not specify an official supported-language list or count; do not mistake languages in cited studies or examples for supported languages.
**Nearest relevant evidence (not a gold answer):** 2410.00037v1, PDF p. 3, section Front matter, R1 chunk 9dbc4fd644b48039: We present Helium, a 7B-parameter text LLM that we pretrain on 2.1T tokens of public English data.

### Q33 - unanswerable
**Question:** What exact GPU model and VRAM did the authors use to serve Moshi?
**Gold answer:** The paper acknowledges donated inference compute but does not identify the serving GPU model or VRAM.
**Expected refusal:** Say the serving GPU model and VRAM are not given; do not infer them from training hardware.
**Nearest relevant evidence (not a gold answer):** 2410.00037v1, PDF p. 42, section Front matter, R1 chunk 9c544074eff7c97c: Hugging Face for inference compute donation.

### Q34 - unanswerable
**Question:** What exact GPU model did the H-Net authors use for XL training, and how many wall-clock hours did that run take?
**Gold answer:** The H-Net paper describes FLOP-matched model budgets but does not state this XL run’s GPU model or wall-clock duration.
**Expected refusal:** Say that neither requested operational detail is specified; do not infer from FLOP budgets.
**Nearest relevant evidence (not a gold answer):** 2507.07955v2, PDF p. 11, section 1024 , 1536 , 2048, R1 chunk c1308d2926dc2036: For calculating FLOPs, we follow standard methodology.

### Q35 - unanswerable
**Question:** How many kilowatt-hours of electricity did the Transformer training run consume?
**Gold answer:** The paper reports GPU count and run duration but not measured electricity use or GPU power draw, so kWh cannot be determined from the paper.
**Expected refusal:** State that electricity consumption is not reported; do not estimate it from GPU count and duration.
**Nearest relevant evidence (not a gold answer):** 1706.03762v7, PDF p. 7, section 5.2 Hardware and Schedule, R1 chunk a74289279fd72be7: We trained our models on one machine with 8 NVIDIA P100 GPUs. For our base models using the hyperparameters described throughout the paper, each training step took about 0.4 seconds.
