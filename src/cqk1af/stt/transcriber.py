"""Transcriber: VAD utterances → STT → bus events.

Subscribes to no events. Instead, consumers (e.g. the audio pipeline orchestrator)
feed ``Utterance`` objects to ``transcribe_utterance``, which runs STT in a
background task and publishes ``TranscriptFinal`` (and downstream
``CallsignHeard``) events on the bus.

When ``TranscriberConfig.extract_callsigns`` is true (the default) every
transcript is passed through ``nlp.callsign_extractor`` and each candidate
above ``min_callsign_confidence`` is emitted as a ``CallsignHeard`` event;
``app.py`` subscribes to those and appends them to ``session.pileup`` when
the FSM is in a state where callers are expected.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass

from ..audio.vad import Utterance
from ..events import CallsignHeard, EventBus, TranscriptDropped, TranscriptFinal
from ..nlp.callsign_extractor import extract_callsigns
from ..nlp.lingo_corrector import correct_lingo
from ..util.logging import get_logger
from .hallucination import is_likely_hallucination
from .prompt_builder import PromptBuilder, Stage
from .whisper_engine import STTEngine, TranscriptionResult

log = get_logger(__name__)


@dataclass
class TranscriberConfig:
    own_callsign: str = ""
    extra_prompt: str = ""
    extra_hotwords: str = ""
    min_text_chars: int = 1
    min_confidence: float = 0.0
    extract_callsigns: bool = True
    min_callsign_confidence: float = 0.5
    drop_hallucinations: bool = True


class Transcriber:
    def __init__(
        self,
        bus: EventBus,
        engine: STTEngine,
        prompt_builder: PromptBuilder | None = None,
        cfg: TranscriberConfig | None = None,
    ) -> None:
        self.bus = bus
        self.engine = engine
        self.prompts = prompt_builder or PromptBuilder()
        self.cfg = cfg or TranscriberConfig()
        self._inflight: set[asyncio.Task] = set()
        self._stage: Stage | None = None

    def set_stage(self, stage: Stage | None) -> None:
        """Hint the current session stage for prompt biasing.

        Callers (typically the audio pipeline reacting to ``StateChanged``)
        update this so the next transcription's prompt is stage-specific.
        """
        self._stage = stage

    async def transcribe_utterance(self, utt: Utterance) -> TranscriptionResult | None:
        prompt, hotwords = self.prompts.build(
            extra=self.cfg.extra_prompt,
            extra_hotwords=self.cfg.extra_hotwords,
            own_callsign=self.cfg.own_callsign or None,
            stage=self._stage,
        )
        try:
            result = await self.engine.transcribe(
                utt.pcm,
                sample_rate=utt.sample_rate,
                initial_prompt=prompt,
                hotwords=hotwords,
            )
        except Exception as e:
            log.exception("stt.transcribe_failed")
            await self.bus.publish(
                TranscriptDropped(reason=f"transcribe_error: {e}", text="")
            )
            return None
        text = correct_lingo(result.text.strip())
        if len(text) < self.cfg.min_text_chars:
            await self.bus.publish(
                TranscriptDropped(
                    reason="empty_or_too_short",
                    text=text,
                    no_speech_prob=result.no_speech_prob,
                )
            )
            return None
        if self.cfg.drop_hallucinations:
            audio_duration_s = len(utt.pcm) / 2.0 / max(1, utt.sample_rate)
            drop, reason = is_likely_hallucination(
                text,
                no_speech_prob=result.no_speech_prob,
                audio_duration_s=audio_duration_s,
            )
            if drop:
                log.info(
                    "stt.hallucination_dropped",
                    text=text,
                    reason=reason,
                    no_speech_prob=result.no_speech_prob,
                )
                await self.bus.publish(
                    TranscriptDropped(
                        reason=f"hallucination:{reason}",
                        text=text,
                        no_speech_prob=result.no_speech_prob,
                    )
                )
                return None
        if result.confidence < self.cfg.min_confidence:
            log.info(
                "stt.low_confidence",
                text=text,
                confidence=result.confidence,
                no_speech_prob=result.no_speech_prob,
            )
        await self.bus.publish(
            TranscriptFinal(text=text, confidence=result.confidence)
        )

        if self.cfg.extract_callsigns:
            own = (self.cfg.own_callsign or "").upper()
            for cand in extract_callsigns(text):
                if cand.confidence < self.cfg.min_callsign_confidence:
                    continue
                if own and cand.callsign.split("/")[0] == own:
                    # We don't want to "hear" our own callsign as a reply.
                    continue
                # Remember the callsign so future prompts bias toward it
                self.prompts.remember_callsign(cand.callsign)
                await self.bus.publish(
                    CallsignHeard(
                        callsign=cand.callsign,
                        confidence=cand.confidence,
                        source_text=text,
                    )
                )
        return result

    def submit(self, utt: Utterance) -> asyncio.Task:
        """Fire-and-forget transcription scheduled on the current loop."""
        task = asyncio.create_task(self.transcribe_utterance(utt))
        self._inflight.add(task)
        task.add_done_callback(self._inflight.discard)
        return task

    async def drain(self) -> None:
        if not self._inflight:
            return
        await asyncio.gather(*list(self._inflight), return_exceptions=True)

    async def aclose(self) -> None:
        await self.drain()
        await self.engine.aclose()
