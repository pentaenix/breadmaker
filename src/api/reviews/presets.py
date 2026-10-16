from __future__ import annotations
"""Review-cycle templates and population routes."""
from fastapi import APIRouter, HTTPException
from api.reviews.contracts import ReviewPopulateIn
from backend.data_access import allrows, one, project
from storage import mutations as storage_mutations
from storage import values as storage_values
from . import domain as review_domain
from . import lifecycle as review_lifecycle
router=APIRouter()

_REVIEW_CYCLE_TEMPLATES={
    'alpha':{
        'name':'Alpha Reading',
        'description':'An early reader pass for premise, clarity, major confusion, pacing, and whether the story is working before deeper revision.',
        'permission_read':True,'permission_comment':True,'permission_suggest':False,'permission_feedback':True,
    },
    'developmental':{
        'name':'Developmental Review',
        'description':'A big-picture pass for structure, pacing, character arcs, clarity, and story logic.',
        'permission_read':True,'permission_comment':True,'permission_suggest':False,'permission_feedback':True,
    },
    'authenticity':{
        'name':'Sensitivity / Authenticity Review',
        'description':'A focused pass for identity, lived experience, cultural context, representation, and authenticity concerns.',
        'permission_read':True,'permission_comment':True,'permission_suggest':False,'permission_feedback':True,
    },
    'technical':{
        'name':'Technical / Subject Matter Review',
        'description':'A specialist pass for factual accuracy, terminology, procedures, continuity, and domain-specific details.',
        'permission_read':True,'permission_comment':True,'permission_suggest':True,'permission_feedback':True,
    },
    'beta':{
        'name':'Beta Reading',
        'description':'A reader-response pass for engagement, confusion, expectations, emotional beats, and overall experience.',
        'permission_read':True,'permission_comment':True,'permission_suggest':False,'permission_feedback':True,
    },
    'line':{
        'name':'Line Review',
        'description':'A sentence-level pass for clarity, voice, rhythm, repetition, and precise wording.',
        'permission_read':True,'permission_comment':True,'permission_suggest':True,'permission_feedback':False,
    },
    'copy':{
        'name':'Copy Edit',
        'description':'A mechanical pass for grammar, usage, consistency, spelling, punctuation, and style-guide adherence.',
        'permission_read':True,'permission_comment':True,'permission_suggest':True,'permission_feedback':False,
    },
    'proof':{
        'name':'Proofreading',
        'description':'A final-pass review for typos, punctuation, spelling, formatting, and small correctness issues.',
        'permission_read':True,'permission_comment':True,'permission_suggest':True,'permission_feedback':False,
    },
}

_REVIEW_POPULATE_PRESETS={
    'reader':{
        'name':'Reader Feedback',
        'description':'A lightweight outside-reader cycle for early reactions and later reader experience.',
        'reviews':['alpha','beta'],
    },
    'editorial':{
        'name':'Editorial Cycle',
        'description':'A practical editing sequence from big-picture revision through sentence mechanics and final proof.',
        'reviews':['developmental','line','copy','proof'],
    },
    'full':{
        'name':'Full Review Cycle',
        'description':'A complete fiction-oriented path from early readers through structural revision, beta feedback, editing, and proof.',
        'reviews':['alpha','developmental','beta','line','copy','proof'],
    },
    'specialist':{
        'name':'Specialist Review',
        'description':'Focused expert passes for authenticity and factual or domain-specific accuracy.',
        'reviews':['authenticity','technical'],
    },
}

@router.get('/api/reviews/populate-options')
def review_populate_options():
    return [{'id':key,**value,'items':[{'id':item,**_REVIEW_CYCLE_TEMPLATES[item]} for item in value['reviews']]} for key,value in _REVIEW_POPULATE_PRESETS.items()]

@router.post('/api/reviews/populate')
def populate_reviews(body:ReviewPopulateIn):
    preset_id=str(body.preset_id or '').strip().lower(); preset=_REVIEW_POPULATE_PRESETS.get(preset_id)
    if not preset: raise HTTPException(400,detail='REVIEW_POPULATE_001: Unknown Review populate preset.')
    created=[]
    with storage_mutations.tx(operation='review-populate') as con:
        p=project(con);existing_names=[str(row['name']) for row in allrows(con,"SELECT name FROM reviews WHERE project_id=?",(p['id'],))];position=review_domain._review_next_position(con,p['id'])
        for key in preset['reviews']:
            template=_REVIEW_CYCLE_TEMPLATES[key]
            base_name=review_domain._review_name((body.names or {}).get(key) or template['name'])
            name=review_domain._review_numbered_name(existing_names,base_name)
            description=review_domain._review_description((body.descriptions or {}).get(key) or template['description'])
            t=storage_values.now();rid=storage_values.uid('review_');permissions=review_domain._normalize_review_permissions(template['permission_read'],template['permission_comment'],template['permission_suggest'],template['permission_feedback'])
            con.execute("INSERT INTO reviews(id,project_id,name,description,position,status,reviewer_visibility,scope_type,permission_read,permission_comment,permission_suggest,permission_feedback,created_at,updated_at) VALUES(?,?,?,?,?,'draft','private','manuscript',?,?,?,?,?,?)",(rid,p['id'],name,description,position,permissions['permission_read'],permissions['permission_comment'],permissions['permission_suggest'],permissions['permission_feedback'],t,t))
            created.append(review_lifecycle._review_payload(con,p['id'],one(con,"SELECT * FROM reviews WHERE id=?",(rid,))));existing_names.append(name);position+=1
    return {'created':created,'count':len(created),'preset_id':preset_id}
