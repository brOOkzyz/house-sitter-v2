"""Minimal per-room two-observation confirmation state for layout signatures."""
from __future__ import annotations
from dataclasses import dataclass
@dataclass
class LayoutCandidate:
    candidate_signature: str
    candidate_count: int
    first_seen_step: int
    last_seen_step: int
    confirmed: bool=False
class TwoObservationLayoutFilter:
    def __init__(self): self.candidates: dict[str,LayoutCandidate]={}
    def observe(self, room:str, signature:str, baseline:str, step:int)->dict:
        current=self.candidates.get(room)
        if signature==baseline:
            if current: del self.candidates[room]; return {"event":"candidate_cleared","confirmed":False}
            return {"event":"baseline_match","confirmed":False}
        if current is None or current.candidate_signature!=signature:
            self.candidates[room]=LayoutCandidate(signature,1,step,step);return {"event":"candidate_started","confirmed":False,"candidate_signature":signature,"candidate_count":1,"first_seen_step":step,"last_seen_step":step}
        current.candidate_count+=1;current.last_seen_step=step
        if current.candidate_count>=2: current.confirmed=True;return {"event":"candidate_confirmed","confirmed":True,"candidate_signature":signature,"candidate_count":current.candidate_count,"first_seen_step":current.first_seen_step,"confirmation_step":step}
        return {"event":"candidate_pending","confirmed":False}
