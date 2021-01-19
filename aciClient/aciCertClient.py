# -*- coding: utf-8 -*-
#
# MIT License
# Copyright (c) 2020 Netcloud AG

"""ACI

AciClient for doing Cert based RestCalls to the APIC

NOTES:
openssl req -new -newkey rsa:2048 -days 36500 -nodes -x509 -keyout apicUser.key -out apicUser.crt
"""
import logging
from OpenSSL import crypto
import base64
import requests
import json


class ACICert:
    __logger = logging.getLogger(__name__)

    # ==============================================================================
    # constructor
    # ==============================================================================
    def __init__(self, apicIp, pkPath, certDn):
        self.__logger.debug(f'Constructor called {apicIp} {pkPath} {certDn}')
        self.apicIp = apicIp
        self.baseUrl = 'https://' + self.apicIp + '/api/'
        self.__logger.debug(f'BaseUrl set to: {self.baseUrl}')
        self.pkey = crypto.load_privatekey(crypto.FILETYPE_PEM, open(pkPath, 'rb').read())
        self.certDn = certDn

    # ==============================================================================
    # packCookies
    # ==============================================================================
    def packCookies(self, content) -> {}:
        sigResult = base64.b64encode(crypto.sign(self.pkey, content, 'sha256')).decode()
        # print('sigResultType', type(sigResult), sigResult)
        return {'APIC-Certificate-Fingerprint': 'fingerprint',
                'APIC-Certificate-Algorithm': 'v1.0',
                'APIC-Certificate-DN': self.certDn,
                'APIC-Request-Signature': sigResult}

    # ==============================================================================
    # getJson
    # ==============================================================================
    def getJson(self, uri) -> {}:
        url = self.baseUrl + uri
        self.__logger.debug(f'Get Json called url: {url}')
        content = 'GET/api/' + uri
        cookies = self.packCookies(content)
        r = requests.get(url, cookies=cookies, verify=False)

        # Raise Exception if http Error occurred
        r.raise_for_status()

        self.__logger.info(f'Successful get Data from APIC: {r.json()}')
        return r.json()['imdata']

    # ==============================================================================
    # postJson
    # ==============================================================================
    def postJson(self, jsonData):
        url = self.baseUrl + 'mo.json'
        self.__logger.debug(f'Post Json called data: {jsonData}')
        data = json.dumps(jsonData, sort_keys=True)
        content = 'POST/api/mo.json' + json.dumps(jsonData)
        cookies = self.packCookies(content)
        r = requests.post(url, data=data, cookies=cookies, verify=False)

        # Raise Exception if http Error occurred
        r.raise_for_status()

        if r.status_code == 200:
            self.__logger.info(f'Successful Posted Data to APIC: {r.json()}')
            return r.status_code
        else:
            self.__logger.error(f'Error during get occured: {r.json()}')
            return r.status_code, r.json()['imdata'][0]['error']['attributes']['text']

    # ==============================================================================
    # deleteMo
    # ==============================================================================
    def deleteMo(self, dn):
        self.__logger.debug(f'Delete Mo called DN: {dn}')
        url = self.baseUrl + 'mo/' + dn + '.json'
        content = 'DELETE/api/mo/' + dn + '.json'
        cookies = self.packCookies(content)
        r = requests.delete(url, cookies=cookies, verify=False)

        # Raise Exception if http Error occurred
        r.raise_for_status()

        return r.status_code
