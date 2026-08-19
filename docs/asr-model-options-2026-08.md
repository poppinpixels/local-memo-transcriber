# Dansk møde-ASR - muligheder efter Hviske v5.3

**Undersøgt:** 2026-08-19
**Miljø:** Apple M4 Mac mini, 16 GB RAM, PyTorch 2.10.0, Transformers 4.57.6, MPS aktivt.
**Formål:** Lange danske møder med følsomt indhold behandles lokalt som udgangspunkt.

## Kort konklusion

**Skift ikke blindt fra Hviske v5.3.** På CoRal v3's samtaletest placerer modellens eget kort v5.3 som den bedste undersøgte danske model: 11,35 % CER med beam search mod 11,6 % for Røst v3 Whisper. Men det er ikke det samme som, at den nuværende pipeline udnytter modellen bedst.

Den nuværende pipeline bruger v5.3's højniveau-`transcribe()`-hjælper med greedy decoding. Modelkortet dokumenterer, at den normale `generate()`-vej med `num_beams=5` reducerer gennemsnitlig WER med 0,4 procentpoint, mod ca. 75 % længere køretid. Det er den første A/B-test værd - før et modelskifte.

SyvAI's oprindelige **Hviske v5** er et 2B-checkpoint med bredere træningsdata, men kortet mærker det nu som bevaret til reproducerbarhed og peger direkte på v5.3 som den anbefalede efterfølger. Dets score er beregnet på 200 eksempler pr. datasæt, så den kan ikke sammenlignes direkte med v5.3's fulde CoRal-test.

Den nye **Hviske v5 tiny** er derimod en reel ny mulighed: 263M parametre, destilleret fra syv-transcribe-ensemblen. Den er målrettet kort lyd, har ingen tidsstempler eller diarisering og skal derfor vurderes som et hurtigt, billigt kladdespor - ikke som en dokumenteret erstatning for lange møder.

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
| **Hviske v5** | V5-kortet viser bred træning og 17,1 % WER på et 200-eksemplers CoRal-samtaleudsnit, men anbefaler selv v5.3 som den nyere model. | Samme 2B-klasse og ca. 4,13 GB download som v5.3. | Kun som kontrol på de samme klip, hvis den brede træning viser sig at passe bedre til dine møder. |
| **Hviske v5 tiny** | 263M-parametre destillat. Kortet viser bl.a. 7,15 % WER på FTSpeech, men 26,07 % WER på CoRal conversation. Målet er fart og lille footprint, ikke ensartet mødekvalitet. | MLX int4 er 179 MB og angives til 87× realtid på en base-M4. Maks. 35-sekunders klip, ingen tidsstempler eller diarisering. | Bedst som hurtig lokal kladde eller mobil/laptop-spor - ikke default for lange møder. |
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
3. Kør de samme klip med Hviske v5 og Røst v3 Whisper. Test ikke en fuld lang optagelse, før ét af dem faktisk vinder på de valgte passager.
4. Kør Hviske v5 tiny på klippenes 30-sekunders segmenter i MLX int4. Mål især fart, transskriptionsfejl og hvor godt segmenterne kan sættes sammen; sammenlign ikke dets FTSpeech-score med mødekvalitet.
5. Vurder ordfejl, navne/fagsprog, hallucinationer/repetitioner, afsnit/tidsstempler og reel behandlingstid. Brug lyd som facit på udvalgte passager - ikke en LLM som dommer.
6. Tilføj først diarisering som et separat trin. Diarisering løser "hvem sagde hvad", ikke ordfejl.
7. Send ikke chef-, CIU- eller kundemøder til en cloududbyder under testen. Brug et optagelsesklip, du eksplicit kan sende ud af huset, hvis cloudsporet skal måles.

## Kilder

- [SyvAI - Hviske v5 modelkort](https://huggingface.co/syvai/hviske-v5)
- [SyvAI - Hviske v5 tiny modelkort](https://huggingface.co/syvai/hviske-v5-tiny)
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
