from __future__ import annotations

from typing import Optional
from pydantic import Field
from api.shared_contracts import BaseModel, Reorder
from formats import breadreview

class BreadReviewPassphraseIn(BaseModel):
    passphrase: str = Field(default='', max_length=breadreview.MAX_PASSPHRASE_CHARS)

class BreadReviewExportIn(BaseModel):
    passphrase: str = Field(default='', max_length=breadreview.MAX_PASSPHRASE_CHARS)

class ReviewParticipantCreate(BaseModel): display_name:str

class ReviewParticipantUpdate(BaseModel): display_name:Optional[str]=None

class ReviewParticipantStatusUpdate(BaseModel): status:str

class ReviewFeedbackStatusUpdate(BaseModel): status:str

class ReviewTaskCreate(BaseModel): title:str; description:str=''

class ReviewSelectionIn(BaseModel):
    start_block_id:str; end_block_id:str; start_offset:int; end_offset:int

class ReviewCommentCreate(BaseModel):
    selection:ReviewSelectionIn; body:str

class ReviewHighlightCreate(BaseModel):
    selection:ReviewSelectionIn; marker_id:Optional[int]=None

class ReviewSuggestionCreate(BaseModel):
    selection:ReviewSelectionIn; operation:str='replace'; proposed_text:str=''

class ReviewQuestionCreate(BaseModel):
    prompt:str; required:bool=False

class ReviewQuestionUpdate(BaseModel):
    prompt:Optional[str]=None; required:Optional[bool]=None

class QuestionnaireCreate(BaseModel):
    name:str; description:str=''

class QuestionnaireUpdate(BaseModel):
    name:Optional[str]=None; description:Optional[str]=None

class QuestionnairePopulateIn(BaseModel):
    name:str; description:str=''; questions:list[str]=Field(default_factory=list)

class QuestionnaireQuestionCreate(BaseModel):
    prompt:str

class QuestionnaireQuestionUpdate(BaseModel):
    prompt:Optional[str]=None

class ReviewQuestionnaireAttach(BaseModel):
    questionnaire_id:str

class ReviewParticipantsPopulate(BaseModel):
    source_review_id:str

class ReviewPopulateIn(BaseModel):
    preset_id:str; names:dict[str,str]={}; descriptions:dict[str,str]={}

class ReviewReorder(BaseModel):
    ids:list[str]

class ReviewFeedbackAnswerIn(BaseModel):
    question_id:str; answer_text:str=''

class ReviewFeedbackSubmit(BaseModel):
    answers:list[ReviewFeedbackAnswerIn]=[]; general_feedback:str=''

class ReviewCreate(BaseModel):
    name:str; description:str=''; scope_type:str='manuscript'; scope_ids:list[int]=[]
    permission_read:bool=True; permission_comment:bool=False; permission_suggest:bool=False; permission_feedback:bool=False
    reviewer_visibility:str='private'; marker_ids:list[int]=[]

class ReviewUpdate(BaseModel):
    name:Optional[str]=None; description:Optional[str]=None; scope_type:Optional[str]=None; scope_ids:Optional[list[int]]=None
    permission_read:Optional[bool]=None; permission_comment:Optional[bool]=None; permission_suggest:Optional[bool]=None; permission_feedback:Optional[bool]=None
    reviewer_visibility:Optional[str]=None; marker_ids:Optional[list[int]]=None

class ReviewMarkerCreate(BaseModel): name:str; description:str=''; color:str

class ReviewMarkerUpdate(BaseModel): name:Optional[str]=None; description:Optional[str]=None; color:Optional[str]=None

class ReviewMarkerSeed(BaseModel): name:str; description:str=''; color:str

class ReviewMarkerPopulateIn(BaseModel): markers:list[ReviewMarkerSeed]=Field(default_factory=list)
