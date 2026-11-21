# Trip personality scoring: research notes

Question: how should a chat trip planner turn a traveler's free-text messages into scores on six scales (pace, money, energy, crowds, food, comfort), each with a confidence? And how should those scores pick and present the three draft plans?

Date: 2026-11-22. Sources are linked inline and listed at the end. "Inference" marks my own reasoning where no source says it directly.

## Bottom line

- **Keep the current shape: the model proposes, code verifies.** The model names a scale, a small signed integer and the traveler's exact words; code checks the quote and decides how much it counts. Gemini's own docs say schema-valid output can still be wrong and must be checked in code ([Gemini structured output](https://aistudio.google.com/docs/structured-output)).
- **Integers −2..+2 are a good idea; floats are not.** LLM scorers produce integers anyway and bunch on one value ([G-Eval, §2](https://arxiv.org/html/2303.16634v3)). Make 0 illegal (no signal means no entry) and let code, not the model, set the size when the quote has "really" or "a bit".
- **Never take confidence from the model.** Verbalized confidence is overconfident ([Xiong et al., ICLR 2024](https://arxiv.org/abs/2306.13063)). Sampling helps, but costs extra calls we can't afford. Build confidence in code from evidence: how many separate statements, whether they agree, hedges, and the source.
- **Accumulate, don't overwrite.** Keep per-scale evidence (a signed sum and a weight) across turns. Confidence grows with evidence and drops with conflict. An explicit correction ("actually, slower") or a tap on the card replaces the old evidence.
- **"One word moves several scales" is allowed only through a code-owned table.** The model may report a second scale for the same quote, but code accepts it only if that pair is in a short list (e.g. "luxury" → comfort, and a weaker money lean), capped at ±1 and never enough alone to mark the scale "known". This blocks small-model nonsense like "Bali" → luxury.
- **Our six scales are reasonable but are not an established typology.** They overlap with Plog's venturer–dependable axis (crowds), the "sun & chill-out" and "action & fun" factors of the seven-factor model, and the slow-travel literature. Nothing in the literature has "money to the stay vs experiences"; that one is a product choice. Treat the scales as our own, and test them on users.
- **For the three drafts: two that fit, one that probes.** Build them to agree on the known scales and differ on the most uncertain one, and label that difference. The pick is the answer to an unasked question (active preference elicitation; [Christakopoulou et al., KDD 2016](https://research.google/pubs/towards-conversational-recommender-systems/)).
- **Make the card scrutable.** Show "because you said '…'" with the verified quote, show unknown scales as unknown, and let the traveler drag or tap a scale to correct it ([Kay 2006](https://researchr.org/publication/Kay06); [Tintarev & Masthoff 2007](https://dblp.uni-trier.de/rec/html/conf/icde/TintarevM07)).

## 1. LLM techniques for graded preferences

**Aspect-based sentiment analysis (ABSA).** Our task is ABSA with fixed aspects: find the aspect (a scale) and the direction (a pole) in a text. The SemEval-2014 ABSA task set this up for restaurant and laptop reviews ([Pontiki et al. 2014](https://aclanthology.org/S14-2004/)). An LLM-era check found LLMs do well on plain sentiment but lag fine-tuned small models on structured ABSA that needs exact spans ([Zhang et al. 2023](https://arxiv.org/abs/2305.15005)). Our "fixed aspect list + quote" design is the easy version of ABSA, which fits a small model.

**Evidence spans and grounding.** Asking for a quote and checking it in code is the attribution idea: a claim counts only if a source supports it ([Rashkin et al., AIS](https://arxiv.org/abs/2112.12870); [Gao et al., ALCE](https://arxiv.org/abs/2305.14627)). An exact-substring check is the cheapest attribution check there is. It proves the words exist. It does not prove they mean what the model says. That is why the code also checks the word list direction and rejects vague-only quotes. The current `read_message` does both.

**Hedges and intensity.** The CoNLL-2010 shared task framed hedge detection as finding cue words ("may", "possibly", "suggest") and their scope in a sentence ([Farkas et al. 2010](https://aclanthology.org/W10-3001/)). Cue-word lists were a strong baseline there. Our `_UNSURE` and `_MILD`/`_STRONG` regexes are this approach. Scope matters: "maybe Lisbon, but definitely slow" has a hedge that is not about pace. Our clause split handles the common case. Inference: hedges and intensifiers are better handled in code than by the model, because they are short closed word lists and the code result is repeatable.

**Calibrated confidence.**
- Verbalized confidence ("I'm 80% sure") is overconfident; consistency across several samples helps; no single method wins ([Xiong et al. 2024](https://arxiv.org/abs/2306.13063)).
- For RLHF models, verbalized confidence is better calibrated than token probabilities ([Tian et al. 2023](https://arxiv.org/abs/2305.14975)). Better than token probabilities is still not good.
- Self-consistency (sample several answers, take the majority) improves reasoning accuracy ([Wang et al. 2023](https://arxiv.org/abs/2203.11171)). It needs N calls per turn.

With one call per turn, sampling is out in production. Inference: derive confidence from countable things code can see (number of separate verified statements, agreement, hedges, source), not from the model.

**Ordinal vs continuous scores.** G-Eval reports that "LLMs usually only output integer scores" and that one value often dominates, so it reweights by token probabilities to get finer scores ([Liu et al. 2023, §2](https://arxiv.org/html/2303.16634v3)). We don't get token probabilities from Gemini's structured output in a useful way, so: ask for a few integers, then do the fine-grained part in code. Gemini's schema supports integer `enum` and inclusive `minimum`/`maximum` ([Gemini docs](https://aistudio.google.com/docs/structured-output)). Ollama accepts a JSON schema in `format`, and its docs advise also putting the schema in the prompt and using temperature 0 ([Ollama docs](https://docs.ollama.com/capabilities/structured-outputs)).

## 2. How recommender research models preferences from dialogue

- **Conversational recommender systems (CRS).** The standard survey ([Jannach et al. 2021, ACM CSUR](https://arxiv.org/abs/2004.00646)) frames a CRS as a multi-turn system that elicits preferences, takes feedback and explains. It notes many systems mix free text with buttons and forms. That matches PRODUCT.md: free text plus tap answers.
- **Asking the most useful question.** Christakopoulou et al. pick questions with a probabilistic latent model and bandit-style explore/exploit; they report a 25% gain after only two questions ([KDD 2016](https://research.google/pubs/towards-conversational-recommender-systems/)). This is the method family for issue #20: keep a belief per scale and ask about the one with the most uncertainty that matters most for the plan.
- **Belief updating.** Inference: a simple version of the Bayesian idea fits us. Keep pseudo-counts per scale (evidence toward each pole). Confidence is a function of total evidence. Conflicting evidence widens uncertainty instead of flipping the value.
- **Critiquing.** Critiquing systems let users react to a shown item ("like this, but cheaper"). Inference: our "edit itinerary" and draft picks are critiques. "Make it less packed" is strong pace evidence because it reacts to something concrete.

## 3. Travel typologies vs our six scales

| Research dimension | Source | Our scale |
|---|---|---|
| Allocentric/venturer ↔ psychocentric/dependable (novelty, familiarity, crowds) | Plog 1974, updated 2001 ([citation](https://typeset.io/papers/why-destination-areas-rise-and-fall-in-popularity-2l6oe88ukr)) | crowds (partly) |
| Novelty ↔ familiarity; drifter, explorer, individual and organized mass tourist | Cohen 1972, *Social Research* 39(1) | crowds, comfort (partly) |
| Motive layers: escape, relaxation, relationships, self-development (core), then culture and nature with experience | [Pearce & Lee 2005](https://researchonline.jcu.edu.au/6740) | pace (relaxation), none for "relationships" |
| Seven factors: sun & chill-out, knowledge & travel, independence & history, culture & indulgence, social & sport, action & fun, nature & recreation | [Neidhardt et al. 2015](https://ec.tuwien.ac.at/node/8789) | energy, pace, crowds (partly) |

Findings:
- Plog's model is the closest match, but tests found weak support for linking it to destination choice (Smith 1990, as summarized in a [critical review](https://aseestant.ceon.rs/index.php/turizam/article/view/18835)). Use it as a vocabulary, not a truth.
- The seven-factor model is the most useful precedent. It came from Big Five personality plus Gibson & Yiannakis tourist roles, reduced by factor analysis. It models each traveler as a **mix** of factors, and it scores them from **picture choices**, not text ([Neidhardt et al. 2015](https://ec.tuwien.ac.at/node/8789); [Sertkan et al. 2020](https://arxiv.org/abs/2006.05172)). This supports the photo this-or-that idea in PRODUCT.md.
- What our scales miss: **culture/learning** (history, museums: knowledge & travel), **nature vs city**, and **social/sport/activity**. Our scales cover how the trip is run. They don't cover what it is about. Inference: that is fine for v1 because "what it's about" is better held as activities and places in the profile. If users keep saying "history" or "hiking", consider a seventh scale.
- "Money to the stay vs experiences" has no direct match. It is a product choice. Watch whether travelers can answer it.
- Slow travel is about fewer places, more time and a mindset ([Dickinson & Lumsdon, *Slow Travel and Tourism*, 2010](https://www.routledge.com/Slow-Travel-and-Tourism/Dickinson-Lumsdon/p/book/9781844076543)). It supports pace as a scale. It also says "slow" can be a trip-wide stance, not just fewer things a day.

## 4. Failure modes

**One-off activity taken as a trip trait.** "One big night out" is an episode, not a disposition. The seven-factor work separates long-term traits from short-term roles ([Sertkan et al. 2020](https://arxiv.org/abs/2006.05172)). Nothing we found handles single-activity scope in chat. Inference: keep the code regex (`_ONE_OFF`), and add a `scope` field to the schema (`trip` or `moment`). Code drops `moment`. The model is good at reading scope in context, and the regex catches the rest.

**Invented evidence.** Models write fluent quotes that aren't in the source; attribution work treats unsupported text as a failure ([Rashkin et al.](https://arxiv.org/abs/2112.12870)). Fix: the exact whole-word check we have, a 6-word cap, and rejecting quotes made only of vague words. Quotes must come from the latest user message only, never from the model's own earlier replies.

**Vague words ("fun").** "Fun" doesn't say which way any scale goes. This is underspecification: the right move is to ask, not guess. CRS question-asking addresses exactly this ([Christakopoulou et al. 2016](https://research.google/pubs/towards-conversational-recommender-systems/)). Keep `_VAGUE`. Inference: treat a vague-only message as a trigger to ask the most uncertain scale question (#20).

**One word, several scales.** Real: "luxury" says comfort strongly and money to the stay weakly. Risk: the model chains inferences ("luxury → spa → slow pace"). Fix: a code-owned table of allowed secondary links, each capped. Anything outside the table is dropped. The existing `_VIBES` table is already this kind of table, for taste.

**Small-model nonsense ("Bali" → luxury).** A place name is not a preference. Expected with an 8B model reading world knowledge as evidence. Fix: the quote must contain at least one non-place, non-vague word (we have this for the destination; extend it to any capitalized place word or a short place list). Inference: the bigger fix is that a quote must survive the word-list direction check or the secondary-link table. Do not let free model guesses through at full weight. A quote that passes the checks but matches no listed word gets ±1 and confidence below `KNOWN`, so it shows as a leaning until confirmed.

## 5. Recommendation for our stack

Constraints: one LLM call per turn (the existing turn call), Gemini flash-lite free tier (the owner reports 15 req/min and 500 req/day; I could not confirm flash-lite numbers on the [rate-limit page](https://aistudio.google.com/docs/rate-limits), which says limits are per project and reset at midnight Pacific), and qwen3:8b locally.

**The owner's proposal** ("give each scale an integer −2..+2 with the user's words as evidence, so one word can tip several scales by different amounts"): good with three changes.
1. Sparse, not dense. Don't ask for all six scales every turn; most would be 0 and small models fill blanks. Ask for a list of signals, usually empty.
2. Code sets the size. The model's ±1/±2 is used only when no intensity word is present; "really", "a bit" and hedges override it.
3. Multi-scale only through the allowed-links table, as above.

**Output schema** (replace `ScaleSignal.toward` with):

```json
{"scale_signals": [
  {"scale": "comfort", "score": 2, "quote": "proper luxury", "scope": "trip"}
]}
```

`scale` is an enum of six; `score` is an integer enum `[-2,-1,1,2]`; `quote` is a string; `scope` is `trip` or `moment`. Both Gemini and Ollama can enforce this ([Gemini](https://aistudio.google.com/docs/structured-output), [Ollama](https://docs.ollama.com/capabilities/structured-outputs)). Gemini drops `maxLength`, so check quote length in code.

**Code checks** (most exist already in `read_message`):
1. `scope == trip`; clause not one-off.
2. Quote is whole words of one clause of the latest message, ≤6 words, not only vague or place words.
3. If a listed word is in the quote, its direction must agree with the sign. If the scale differs, the pair must be in the allowed-links table; cap at ±1.
4. Negation rules as now.
5. Size: intensity word → ±2 or ±1; else the model's score; unlisted quote → ±1.

**Confidence and accumulation** (inference; a pseudo-count version of Bayesian updating):
- Each accepted signal adds evidence `w` toward its sign: listed word 1.0, model-only 0.5, secondary link 0.3, hedged ×0.4. Card answers (#20) 1.5. Taps on the card replace everything.
- Per scale keep `pos` and `neg` (summed `w × |score|/2`).
- `value = (pos − neg) / (pos + neg)`, scaled by the strongest magnitude seen.
- `confidence = (pos + neg) / (pos + neg + 1) × agreement`, where `agreement = |pos − neg| / (pos + neg)`. One strong (±2) listed statement gives 0.5 (just "known"), two give about 0.67, and a conflict pulls it down.
- Taste seeds start as a small prior (`w = 0.3`), as now.
- An explicit correction ("actually", "no, I meant", "less X") clears that scale's message evidence before adding.

This changes "latest message wins" into "evidence adds up". That is the one real behavior change from today. It makes "slow… slow… one night out" stay slow.

**Cheap evaluation.**
- 60–100 single sentences, each labelled per scale with `{−2..2 or none}`, plus scope. About 40% should have no signal (vague, one-off, place-only, negated) because false positives are the main risk.
- Metrics: false-positive rate on "none" (target under 5%), sign accuracy on labelled signals, quote-valid rate (before code checks, to watch the model), and the share caught by code.
- Run both models at temperature 0. Offline only, run each sentence three times to measure stability (self-consistency as a test, not as a production feature; [Wang et al.](https://arxiv.org/abs/2203.11171)). At 15 req/min, 100 sentences take about 7 minutes and use 100 of the 500 daily requests.
- Add 10 multi-turn scripts to test accumulation and correction.

**Trade-offs.** Code-owned tables miss real phrasing ("let's not rush" is slow). This is deliberate: misses fall back to questions, which is the product's design, while wrong reads mislead the plan. The pseudo-count weights are made up; tune them on the test set. qwen3:8b will produce more rejected signals than Gemini; the checks make that a recall loss, not a correctness loss.

## 6. How the scores should drive which trips we show and how

**(a) Diversity vs pure top-k.** Three drafts from pure top-k by fit tend to be near copies. MMR picks each next item by relevance minus similarity to what's already picked, with λ trading the two ([Carbonell & Goldstein 1998](https://www.cs.cmu.edu/~jgc/publication/MMR_DiversityBased_Reranking_SIGIR_1998.pdf)). Calibrated recommendations go further: the mix in a list should match the mix of the user's interests, measured with KL divergence and built greedily ([Steck 2018](https://dl.acm.org/doi/10.1145/3240323.3240372)). Inference for us: inside one draft, calibrate. A "food matters a little" traveler (food +0.4) should get a few food-led stops, not every meal at a tasting menu. Across the three drafts, diversify, but only on scales we are unsure about.

**(b) Explanations.** Tintarev & Masthoff list seven aims for explanations, including transparency, scrutability and trust ([2007](https://dblp.uni-trier.de/rec/html/conf/icde/TintarevM07)). Inference: a trait badge with the verified quote ("Slow pace, because you said 'really slow mornings'") serves transparency and trust, and it's cheap: the quote is already checked. Only quote verified words. Never explain from a taste seed as if the traveler had said it; say "from your past trips".

**(c) Showing uncertainty.** Show unknown scales as unknown, not as a middle guess. A middle value looks like a preference. Low-confidence taste leanings can show as "guess". For the drafts, use the most uncertain scale that changes the plan most as the axis of difference: e.g. one draft "Packed days", one "Slow pace", both matching everything known. This is the explore part of explore/exploit in CRS question selection ([Christakopoulou et al. 2016](https://research.google/pubs/towards-conversational-recommender-systems/)), done with plans instead of a question. Picking a draft is then evidence for that scale (weight like a card answer). Inference: only the draft pick counts, not views or scrolls.

**(d) Direct correction.** Scrutable user models let people see and change what the system believes about them, and Kay argues adaptive systems must offer this ([Kay 2006](https://researchr.org/publication/Kay06)). PRODUCT.md already has a "What Adieu knows about me" page. Do the same per trip: each scale chip on the card is tappable to move it or to say "not sure". A tap is `source: "user"`, confidence 1.0, and model reads never overwrite it.

**Concrete layout for the 3 drafts and the card.**
- Draft 1, **best fit**: matches every known scale. Unknown scales sit at the middle.
- Drafts 2 and 3, **the probe**: same as draft 1 on known scales, opposite poles on the single most uncertain scale. Header names the difference in plain words ("Same trip, more nights out" / "…, quiet evenings").
- If no scale is uncertain, drafts 2 and 3 differ on the least-confident known scale, or by area/route (MMR on places).
- Each draft shows up to three trait badges, each with "because you said '…'" or "guess" or "you set this".
- The personality card lists all six scales: known ones as a position with the quote, unknown ones as "not sure yet" with a tap to answer, all tappable to correct.

## Sources

- Carbonell & Goldstein 1998, MMR, SIGIR. https://www.cs.cmu.edu/~jgc/publication/MMR_DiversityBased_Reranking_SIGIR_1998.pdf
- Christakopoulou, Radlinski, Hofmann 2016, Towards Conversational Recommender Systems, KDD. https://research.google/pubs/towards-conversational-recommender-systems/
- Cohen 1972, Toward a Sociology of International Tourism, *Social Research* 39(1):164–182 (not fetched; cited from standard references).
- Dickinson & Lumsdon 2010, *Slow Travel and Tourism*, Earthscan (not fetched). https://www.routledge.com/Slow-Travel-and-Tourism/Dickinson-Lumsdon/p/book/9781844076543
- Farkas et al. 2010, CoNLL-2010 shared task on hedges and their scope. https://aclanthology.org/W10-3001/
- Gao et al. 2023, Enabling LLMs to Generate Text with Citations (ALCE). https://arxiv.org/abs/2305.14627
- Google, Gemini structured output. https://aistudio.google.com/docs/structured-output
- Google, Gemini rate limits. https://aistudio.google.com/docs/rate-limits
- Jannach, Manzoor, Cai, Chen 2021, A Survey on Conversational Recommender Systems, ACM CSUR. https://arxiv.org/abs/2004.00646
- Kay 2006, Scrutable Adaptation: Because We Can and Must, AH 2006, LNCS 4018. https://researchr.org/publication/Kay06
- Liu et al. 2023, G-Eval. https://arxiv.org/html/2303.16634v3
- Neidhardt, Seyfang, Schuster, Werthner 2015, A picture-based approach to recommender systems, *Information Technology & Tourism* 15(1). https://ec.tuwien.ac.at/node/8789
- Ollama, Structured outputs. https://docs.ollama.com/capabilities/structured-outputs
- Pearce & Lee 2005, Developing the Travel Career Approach to Tourist Motivation, *J. Travel Research* 43(3). https://researchonline.jcu.edu.au/6740
- Plog 2001, Why Destination Areas Rise and Fall in Popularity, *Cornell HRA Quarterly* 42(3) (full text not fetched). https://typeset.io/papers/why-destination-areas-rise-and-fall-in-popularity-2l6oe88ukr
- Plog's and Butler's models: a critical review (summarizes Smith 1990 and McKercher 2005). https://aseestant.ceon.rs/index.php/turizam/article/view/18835
- Pontiki et al. 2014, SemEval-2014 Task 4: Aspect Based Sentiment Analysis. https://aclanthology.org/S14-2004/
- Rashkin et al. 2023, Measuring Attribution in Natural Language Generation Models (AIS). https://arxiv.org/abs/2112.12870
- Sertkan, Neidhardt, Werthner 2020, Eliciting Touristic Profiles: A User Study on Picture Collections. https://arxiv.org/abs/2006.05172
- Steck 2018, Calibrated Recommendations, RecSys. https://dl.acm.org/doi/10.1145/3240323.3240372
- Tian et al. 2023, Just Ask for Calibration. https://arxiv.org/abs/2305.14975
- Tintarev & Masthoff 2007, A Survey of Explanations in Recommender Systems, ICDE Workshops. https://dblp.uni-trier.de/rec/html/conf/icde/TintarevM07
- Wang et al. 2023, Self-Consistency Improves Chain of Thought Reasoning. https://arxiv.org/abs/2203.11171
- Xiong et al. 2024, Can LLMs Express Their Uncertainty?, ICLR. https://arxiv.org/abs/2306.13063
- Zhang et al. 2023, Sentiment Analysis in the Era of LLMs: A Reality Check. https://arxiv.org/abs/2305.15005
