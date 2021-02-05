# -*- coding: utf-8 -*-
#
# MIT License
# Copyright (c) 2020 Netcloud AG

"""AciClient Testing

"""
from requests import RequestException

from aciClient.aci import ACI
import pytest

__BASE_URL = 'testing-apic.ncdev.ch'


def test_login_ok(requests_mock):
    requests_mock.post(f'https://{__BASE_URL}/api/aaaLogin.json', json={'imdata': [
        {'aaaLogin': {'attributes': {'token': 'asdafa'}}}
    ]})
    aci = ACI(apicIp=__BASE_URL, apicUser='admin', apicPasword='unkown')
    assert aci.login()


def test_token_ok(requests_mock):
    requests_mock.post(f'https://{__BASE_URL}/api/aaaLogin.json', json={'imdata': [
        {'aaaLogin': {'attributes': {'token': 'tokenxyz'}}}
    ]})
    aci = ACI(apicIp=__BASE_URL, apicUser='admin', apicPasword='unkown')
    aci.login()
    token = aci.getToken()
    assert token == 'tokenxyz'


def test_login_401(requests_mock):
    requests_mock.post(f'https://{__BASE_URL}/api/aaaLogin.json', json={'status_code': 401, 'text': 'not allowed'},
                       status_code=401)
    aci = ACI(apicIp=__BASE_URL, apicUser='admin', apicPasword='unkown')
    assert not aci.login()


def test_login_404_exception(requests_mock):
    requests_mock.post(f'https://{__BASE_URL}/api/aaaLogin.json', json={'status_code': 404, 'text': 'Not Found'},
                       status_code=404)
    aci = ACI(apicIp=__BASE_URL, apicUser='admin', apicPasword='unkown')
    with pytest.raises(RequestException):
        resp = aci.login()


def test_renew_cookie_ok(requests_mock):
    requests_mock.post(f'https://{__BASE_URL}/api/aaaLogin.json', json={'imdata': [
        {'aaaLogin': {'attributes': {'token': 'tokenxyz'}}}
    ]})
    requests_mock.post(f'https://{__BASE_URL}/api/aaaRefresh.json', json={'imdata': [
        {'aaaLogin': {'attributes': {'token': 'tokenxyz'}}}
    ]},
                       status_code=200)
    aci = ACI(apicIp=__BASE_URL, apicUser='admin', apicPasword='unkown')
    aci.login()
    assert aci.renewCookie()


def test_renew_cookie_exception(requests_mock):
    requests_mock.post(f'https://{__BASE_URL}/api/aaaLogin.json', json={'imdata': [
        {'aaaLogin': {'attributes': {'token': 'tokenxyz'}}}
    ]})
    requests_mock.post(f'https://{__BASE_URL}/api/aaaRefresh.json', json={'status_code': 401, 'text': 'Not Allowed'},
                       status_code=401)
    aci = ACI(apicIp=__BASE_URL, apicUser='admin', apicPasword='unkown')
    aci.login()
    with pytest.raises(RequestException):
        aci.renewCookie()


def test_get_tenant_ok(requests_mock):
    uri = 'mo/uni/tn-common.json'
    requests_mock.post(f'https://{__BASE_URL}/api/aaaLogin.json', json={'imdata': [
        {'aaaLogin': {'attributes': {'token': 'tokenxyz'}}}
    ]})
    requests_mock.get(f'https://{__BASE_URL}/api/{uri}',
                      json={'imdata': [{'fvTenant': {'attributes':
                                                         {'annotation': '',
                                                          'childAction': '',
                                                          'descr': '',
                                                          'dn': 'uni/tn-common',
                                                          'extMngdBy': '',
                                                          'lcOwn': 'local',
                                                          'modTs': '2020-11-23T15:53:52.014+00:00',
                                                          'monPolDn': 'uni/tn-common/monepg-default',
                                                          'name': 'common',
                                                          'nameAlias': '',
                                                          'ownerKey': '',
                                                          'ownerTag': '',
                                                          'status': '',
                                                          'uid': '0'}}}]
                            })
    aci = ACI(apicIp=__BASE_URL, apicUser='admin', apicPasword='unkown')
    aci.login()
    resp = aci.getJson(uri)
    assert 'fvTenant' in resp[0]


def test_get_tenant_ok(requests_mock):
    uri = 'mo/uni/tn-common.json'
    requests_mock.post(f'https://{__BASE_URL}/api/aaaLogin.json', json={'imdata': [
        {'aaaLogin': {'attributes': {'token': 'tokenxyz'}}}
    ]})
    requests_mock.get(f'https://{__BASE_URL}/api/{uri}',
                      json={'imdata': [{'fvTenant': {'attributes':
                                                         {'annotation': '',
                                                          'childAction': '',
                                                          'descr': '',
                                                          'dn': 'uni/tn-common',
                                                          'extMngdBy': '',
                                                          'lcOwn': 'local',
                                                          'modTs': '2020-11-23T15:53:52.014+00:00',
                                                          'monPolDn': 'uni/tn-common/monepg-default',
                                                          'name': 'common',
                                                          'nameAlias': '',
                                                          'ownerKey': '',
                                                          'ownerTag': '',
                                                          'status': '',
                                                          'uid': '0'}}}]
                            })
    aci = ACI(apicIp=__BASE_URL, apicUser='admin', apicPasword='unkown')
    aci.login()
    resp = aci.getJson(uri)
    assert 'fvTenant' in resp[0]


def test_get_tenant_not_found(requests_mock):
    uri = 'mo/uni/tn-commmmmon.json'
    requests_mock.post(f'https://{__BASE_URL}/api/aaaLogin.json', json={'imdata': [
        {'aaaLogin': {'attributes': {'token': 'tokenxyz'}}}
    ]})
    requests_mock.get(f'https://{__BASE_URL}/api/{uri}',
                      json={'error': 'Not found'}, status_code=404)
    aci = ACI(apicIp=__BASE_URL, apicUser='admin', apicPasword='unkown')
    aci.login()
    resp = aci.getJson(uri)
    assert 'error' in resp


def test_post_tenant_ok(requests_mock):
    post_data = {'fvTenant': {'attributes':
                                  {'descr': '',
                                   'dn': 'uni/tn-test',
                                   'status': 'modified,created'}}
                 }
    requests_mock.post(f'https://{__BASE_URL}/api/aaaLogin.json', json={'imdata': [
        {'aaaLogin': {'attributes': {'token': 'tokenxyz'}}}
    ]})
    requests_mock.post(f'https://{__BASE_URL}/api/mo.json',
                       json={'imdata': post_data})
    aci = ACI(apicIp=__BASE_URL, apicUser='admin', apicPasword='unkown')
    aci.login()
    resp = aci.postJson(post_data)
    assert resp == 200


def test_post_tenant_bad_request(requests_mock):
    post_data = {'fvTenFailFailant': {'attributes':
                                          {'descr': '',
                                           'dn': 'uni/tn-test',
                                           'status': 'modified,created'}}
                 }
    requests_mock.post(f'https://{__BASE_URL}/api/aaaLogin.json', json={'imdata': [
        {'aaaLogin': {'attributes': {'token': 'tokenxyz'}}}
    ]})
    requests_mock.post(f'https://{__BASE_URL}/api/mo.json',
                       json={'imdata': [
                           {'error': {'attributes': {'text': 'tokenxyz'}}}]},
                       status_code=400)
    aci = ACI(apicIp=__BASE_URL, apicUser='admin', apicPasword='unkown')
    aci.login()
    resp = aci.postJson(post_data)
    assert resp.startswith('400: ')


def test_post_tenant_forbidden_exception(requests_mock):
    post_data = {'fvTenFailFailant': {'attributes':
                                          {'descr': '',
                                           'dn': 'uni/tn-test',
                                           'status': 'modified,created'}}
                 }
    requests_mock.post(f'https://{__BASE_URL}/api/aaaLogin.json', json={'imdata': [
        {'aaaLogin': {'attributes': {'token': 'tokenxyz'}}}
    ]})
    requests_mock.post(f'https://{__BASE_URL}/api/mo.json',
                       json={'imdata': [
                           {'error': {'attributes': {'text': 'tokenxyz'}}}]},
                       status_code=403)
    aci = ACI(apicIp=__BASE_URL, apicUser='admin', apicPasword='unkown')
    aci.login()
    with pytest.raises(RequestException):
        aci.postJson(post_data)
