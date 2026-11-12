# Reflection draft -
## One thing that was new to me

I started this project thinking that a rate limit was a simple gate: a request either gets accepted or rejected, and a 429 means no useful work happened. Running the same questions through R1, R2, and R3 changed that picture. One answer is a chain of retrieval and model steps, and a later repair call can hit a limit after earlier calls have already used tokens. Then, while checking the latency table, I learned a second part of the same lesson: elapsed time on a failed request is not time to first token. I now see why a reliable evaluation needs both a request-level outcome and trace-level evidence. A single “failed” label hides where time and budget went, while a single latency percentile can be misleading if its sample mixes delivered answers with failures.

## My feedback on Vaani

What stood out to me on Vaani’s public site was the “Teacher AI” build flow: it says the agent asks clarifying questions, assembles the prompt and tools, then shows a change diff for approval. That review-before-accepting step feels like a useful product choice because a small prompt change can alter a live agent. The site also presents “Memories” as a way to carry selected context from past calls into the next one. What I could not tell from the homepage was how its readiness score is calculated or how a team decides which past-call details are appropriate to retain. I would add a short, concrete explanation of those two things near the product demo. [Source: Vaani homepage](https://www.vaani.dev/)

## How I worked with the assistant

### The prompts that mattered most

These are cleaned-up versions of the prompts that shaped the work, not verbatim conversation quotes.

1. **Set the objective before picking the design.** “Before you choose an architecture, turn the assignment into measurable goals and constraints. Show me the trade-offs between answer quality, latency, provider limits, and cost. For each major choice, give me at least two options, what evidence would distinguish them, and the smallest safe experiment that could produce that evidence. Keep ambitious ideas if they improve the measured result, but call out their cost and risk.” I would defend this because system design starts by making the competing goals visible; otherwise the assistant can optimize an easy metric while quietly weakening answer quality or safety.
2. **Make comparisons fair and failures informative.** “Design the R1/R2/R3 comparison so the same question set is tested across settings and models. Before every real run, show the per-model request and token estimate, the ledger-based budget, and the stop condition. Keep every 429 and failed attempt in the evidence. Report first-token latency only for answers actually delivered, failure rates over all attempts, and question counts separately from retry counts. If a metric cannot be measured, say so.” I would defend this because controlled comparisons make results explainable, and choosing the right denominator prevents retries or failures from making quality and latency look better than they were.
3. **Make every result auditable end to end.** “Keep the required results CSV contract stable, but persist each request’s question, final answer, retrieved evidence IDs, and stage-level trace under the same request ID. Then have a reviewer recompute the tables from the raw results and traces, and flag missing usage or other unknowns instead of filling gaps with assumptions.” I would defend this because a final answer or aggregate number is not enough to investigate a failure; the request ID should connect the answer, evidence, latency, and token cost without making the primary results file harder to consume.

### One thing the assistant got wrong, and how I caught it

The first cost summary counted the successful request traces but missed known model-call spend from attempts that later failed with a 429 during repair. I asked for a CTO review that recomputed cost from the traces. That review showed that an unsuccessful final answer can still have real upstream token usage. We changed the calculation to include known spend from every attempt, divide by successful answers, and label missing usage as unknown. I also made sure the 429 attempts stayed in the outcomes instead of being mislabeled as wrong answers.

### A decision I overruled

After provider rate limits interrupted the evaluation, I did not accept a stand-in-only comparison. Once the limit reset and I had updated the key, I explicitly told the assistant to complete R2 day 1 and day 2, then run R3 with real calls. I asked it to keep the calls budgeted and the raw outcomes, including 429s, visible. That was my call to spend some of the available quota on comparable real evidence instead of stopping at simulation; it was not permission to push through a limit or exhaust the daily budget.

### What is distinctly mine

I used an assistant to build and inspect the system, but the direction and approvals were mine. I pushed for real R2 and R3 measurements, approved the audit-trace decision, and asked for failures to be reported honestly instead of turned into quality scores. The part I can defend as my own is how I chose what to measure, what evidence I trusted, and where I drew the line between useful technical depth and an unsafe or unsupported claim. I am not claiming that I typed every implementation line myself, I am claiming ownership of the engineering questions, trade-offs, and review decisions.

