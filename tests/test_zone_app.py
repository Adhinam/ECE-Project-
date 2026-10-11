"""Reporting and calibration contracts for the local processing app."""
from types import SimpleNamespace as Obj

from fastapi import HTTPException
import pytest

from zone_app.analytics import VideoAnalytics
from zone_app.server import ROOT, WORKSPACE, output_base, validate_layout


def frame(index, ids=(7,)):
    return Obj(frame_index=index, tracks=[Obj(track_id=i) for i in ids])


def event(kind, identity=7):
    return Obj(event_type=Obj(value=kind), track_id=identity,
               payload={'line_id':'door','visitor_id':'VIS_example'})


def visit(start=0, end=None, seconds=0, sequence=1):
    return Obj(zone_id='dairy',session_sequence=sequence,entered_frame=start,
               exited_frame=end,total_dwell_seconds=seconds)


def test_zone_visits_not_periodic_dwell_sums():
    analytics=VideoAnalytics()
    zones=Obj(histories={7:Obj(visits=[visit()])})
    analytics.update(frame(9),[event('entry'),event('zone_dwell')],zones,10)
    analytics.update(frame(19),[event('zone_dwell')],zones,10)
    report=analytics.report()
    assert report['entries']==1
    assert report['zone_totals']==[{'id':7,'zone':'dairy','dwell_seconds':2}]
    zones.histories[7].visits=[visit(end=20,seconds=2),visit(start=25,sequence=2)]
    analytics.update(frame(29),[event('exit')],zones,10)
    report=analytics.report(complete=True)
    assert report['zone_totals'][0]['dwell_seconds']==2.5
    assert report['visits'][1]['status']=='end of video (partial)'
    assert report['exits']==1
    assert report['crossings'][1]['seconds']==2.9


def test_already_visible_person_and_video_end_do_not_invent_crossings():
    analytics=VideoAnalytics()
    analytics.update(frame(0),[],Obj(histories={7:Obj(visits=[visit()])}),10)
    report=analytics.report(complete=True)
    assert report['entries']==report['exits']==0
    assert report['observed_ids']==1
    assert report['visits'][0]['dwell_seconds']==.1


def test_relative_output_and_workspace_boundary():
    assert output_base('sample_video/output/app_runs')==ROOT/'sample_video/output/app_runs'
    with pytest.raises(HTTPException):
        output_base(str(WORKSPACE.parent/'outside-project'))
    with pytest.raises(HTTPException):
        output_base('.git/output')


def valid_layout():
    return {'store_id':'demo','frame_width':640,'frame_height':480,
            'zones':[{'id':'dairy','polygon':[[10,10],[400,10],[400,400],[10,400]]}],
            'entry_exit_lines':[{'id':'door','p1':[20,420],'p2':[400,420],'direction_in':'bottom'}]}


def test_layout_matches_real_video_and_rejects_bad_geometry():
    spec={'width':640,'height':480}
    assert validate_layout(valid_layout(),spec)['zones'][0]['id']=='dairy'
    data=valid_layout();data['frame_width']=1280
    with pytest.raises(HTTPException):
        validate_layout(data,spec)
    data=valid_layout();data['zones'][0]['polygon']=[[10,10],[400,400],[10,400],[400,10]]
    with pytest.raises(HTTPException):
        validate_layout(data,spec)
    data=valid_layout();data['store_id']='../../events'
    with pytest.raises(HTTPException):
        validate_layout(data,spec)
