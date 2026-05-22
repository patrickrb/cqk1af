"""Transcriber: VAD utterances → STT → bus events.

Subscribes to no events. Instead, consumers (e.g. the audio pipeline orchestrator)
feed ``Utterance`` objects to ``transcribe_utterance``, which runs STT in a
background task and publishes ``TranscriptFinal`` (and downstream
``CallsignHeard``) events on the bus.

Phase 7 wires the basic transcript path. Phase 8 wires the callsign extractor.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass

from ..audio.vad import Utterance
from ..events import CallsignHeard, EventBus, TranscriptFinal
from ..nlp.callsign_extractor import extract_callsigns
from ..util.logging import get_logger
from .prompt_builder import PromptBuilder
from .whisper_engine import STTEngine, TranscriptionResult

log = get_logger(__name__)


@dataclass
class TranscriberConfig:
    own_callsign: str = ""
    extra_prompt: str = ""
    min_text_chars: int = 1
    min_confidence: float = 0.0
    extract_callsigns: bool = True
    min_callsign_confidence: float = 0.5


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

    async def transcribe_utterance(self, utt: Utterance) -> TranscriptionResult | None:
        prompt = self.prompts.build(extra=self.cfg.extra_prompt, own_callsign=self.cfg.own_callsign or None)
        try:
            result = await self.engine.transcribe(
                utt.pcm, sample_rate=utt.sample_rate, initial_prompt=prompt
            )
        except Exception:
            log.exception("stt.transcribe_failed")
            return None
        text = result.text.strip()
        if len(text) < self.cfg.min_text_chars:
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
