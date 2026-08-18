# Dansk møde-ASR - muligheder efter Hviske v5.3

**Undersøgt:** 2026-08-19  
**Miljø:** Apple M4 Mac mini, 16 GB RAM, PyTorch 2.10.0, Transformers 4.57.6, MPS aktivt.  
**Formål:** Lange danske møder med følsomt indhold behandles lokalt som udgangspunkt.

## Kort konklusion

**Skift ikke blindt fra Hviske v5.3.** På CoRal v3's samtaletest placerer modellens eget kort v5.3 som den bedste undersøgte danske model: 11,35 % CER med beam search mod 11,6 % for Røst v3 Whisper. Men det er ikke det samme som, at den nuværende pipeline udnytter modellen bedst.

Den nuværende pipeline bruger v5.3's højniveau-`transcribe()`-hjælper med greedy decoding. Modelkortet dokumenterer, at den normale `generate()`-vej med `num_beams=5` reducerer gennemsnitlig WER med 0,4 procentpoint, mod ca. 75 % længere køretid. Det er den første A/B-test værd - før et modelskifte.

## Baseline

| Optagelse | Lydlængde | Nuværende behandlingstid | Estimat med beam=5 |
|---|---:|---:|---:|
| Sorø Workshop | 100,0 min. | 13,0 min. | 22,8 min. |
| Møde m Søren CIU | 68,6 min. | 9,3 min. | 16,3 min. |

De to færdige tekster har stadig enkelte korte repetitionsmønstre efter den nuværende oprydning. Det beviser ikke en WER, men det er et reelt signal om, at dekodning og kvalitetstjek bør A/B-testes på rigtige mødeklip.

## Kandidater

| Kandidat | Evidens for dansk mødetale | Praktisk vurdering på M4/16 GB | Anbefaling |
|---|---|---|---|
| **Hviske v5.3 med beam=5** | v5.3-kortet angiver 11,35 % CER på CoRal v3 conversation mod 11,56 % ved greedy decoding. | Samme model og pipelinegrundlag. Langsommere, men de målte møder vil fortsat blive færdige på under 25 minutter. | **Første test.** |
| **CoRal Røst v3 Whisper 1.5B** | 11,6 % CER på samme CoRal conversation-test. Trænet på dansk samtale- og oplæsningsdata. | 3,09 GB download. Standard Transformers Whisper-model, så den er en renere integrationskandidat end et nyt NeMo- eller vLLM-stack. | **Eneste reelle lokale modeludfordrer.** Test på identiske klip. |
| **NVIDIA Canary 1B v2** | Understøtter dansk, punktuation, ord- og segmenttidsstempler. | 6,36 GB download og kræver NeMo; dokumentationen er NVIDIA-GPU/CUDA-orienteret. Ingen direkte Apple MPS-vej er dokumenteret. | Ikke prioritet på denne Mac. |
| **NVIDIA Parakeet RNNT 110M dansk** | 10,7 % WER på CoRal Test i NVIDIA-kortet, men trænings- og evalueringsbeskrivelsen er domineret af oplæst tale. | Kun 0,45 GB, men NeMo og NVIDIA-accelereret deployment. Ingen dokumenteret MPS-vej. | Kun hvis hastighed er vigtigere end mødekvalitet. |
| **Mistral Voxtral Mini Transcribe 2** | Batch-API med diarisation, ordtidsstempler og op til 3 timers optagelser. | Cloudtjeneste til $0,003/minut. Mistral beskriver kontekst-bias for andre sprog end engelsk som eksperimentel; dansk kvalitet skal derfor måles, ikke antages. | God cloud-kandidat, hvis databehandling uden for Mac'en er acceptabel. |
| **OpenAI gpt-transcribe** | Understøtter prompt, nøgleord og sprogangivelse. gpt-4o-transcribe-diarize kan lave talersegmenter. | Cloud, 25 MB filgrænse pr. upload og $0,0045/minut for gpt-transcribe. | Kun selektivt efter databeskyttelsesafklaring og A/B-test. |

## Vigtig licensafgrænsning

Hviske v5.3 er udgivet under **CC BY-NC 4.0**. Det er derfor ikke en sikker standard til betalte kundeopgaver eller kommerciel drift uden særskilt tilladelse. Røst v3-kortet angiver en tilpasset OpenRAIL-M-licens, der tillader kommerciel brug med få begrænsninger. NVIDIA Canary 1B v2 er CC BY 4.0, og Parakeet-kortet angiver kommerciel og ikke-kommerciel brug som tilladt. Dette er en praktisk licensmarkering, ikke juridisk rådgivning.

## Anbefalet, ikke-destruktiv A/B-test

1. Udvælg tre 5-minutters klip med forskellig sværhedsgrad: rolig én-til-én-samtale, flere stemmer/støj og fagsprog/navne.
2. Behold v5.3-greedy som kontrol og transskriber de samme klip med v5.3 `generate()` + `num_beams=5`.
3. Kør de samme klip med Røst v3 Whisper.
4. Vurder ordfejl, navne/fagsprog, hallucinationer/repetitioner, afsnit/tidsstempler og reel behandlingstid. Brug lyd som facit på udvalgte passager - ikke en LLM som dommer.
5. Tilføj først diarisation som et separat trin. Diarisation løser "hvem sagde hvad", ikke ordfejl.
6. Send ikke chef-, CIU- eller kundemøder til en cloududbyder under testen. Brug et optagelsesklip, du eksplicit kan sende ud af huset, hvis cloudsporet skal måles.

## Kilder

- [SyvAI - Hviske v5.3 modelkort](https://huggingface.co/syvai/hviske-v5.3)
- [CoRal - Røst v3 Whisper 1.5B modelkort](https://huggingface.co/CoRal-project/roest-v3-whisper-1.5b)
- [NVIDIA - Parakeet RNNT 110M dansk modelkort](https://huggingface.co/nvidia/parakeet-rnnt-110m-da-dk)
- [NVIDIA - Canary 1B v2 modelkort](https://huggingface.co/nvidia/canary-1b-v2)
- [Mistral - Voxtral Mini Transcribe 2 modelkort](https://docs.mistral.ai/models/model-cards/voxtral-mini-transcribe-26-02)
- [Mistral - offline transskription](https://docs.mistral.ai/studio-api/audio/speech_to_text/offline_transcription)
- [OpenAI - filtransskription](https://platform.openai.com/docs/guides/speech-to-text)
- [OpenAI - aktuel prisside](https://platform.openai.com/docs/pricing)
- [ElevenLabs - Scribe v2 transskription](https://elevenlabs.io/docs/capabilities/speech-to-text)
- [SyvAI - speaker diarization 3.1](https://huggingface.co/syvai/speaker-diarization-3.1)
