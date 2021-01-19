# -*- coding: utf-8 -*-
#
# MIT License
# Copyright (c) 2020 Netcloud AG

"""ACI

AciClient for doing Username/Password based RestCalls to the APIC
"""
import logging
import json
import requests

# The modules are named different in python2/python3...
try:
    from urlparse import urlparse, urlunparse, parse_qsl
    from urllib import urlencode
except ImportError:
    from urllib.parse import urlparse, urlunparse, urlencode, parse_qsl

requests.packages.urllib3.disable_warnings()


class ACI:
    __logger = logging.getLogger(__name__)

    # ==============================================================================
    # constructor
    # ==============================================================================
    def __init__(self, apicIp, apicUser, apicPasword):
        self.__logger.debug('Constructor called')
        self.apicIp = apicIp
        self.apicUser = apicUser
        self.apicPassword = apicPasword

        self.baseUrl = 'https://' + self.apicIp + '/api/'
        self.__logger.debug(f'BaseUrl set to: {self.baseUrl}')

        self.session = None
        self.token = None

    # ==============================================================================
    # login
    # ==============================================================================
    def login(self) -> bool:
        self.__logger.debug('login called')

        self.session = requests.Session()
        self.__logger.info('Session Object Created')

        # create credentials structure
        userPass = json.dumps({'aaaUser': {'attributes': {'name': self.apicUser, 'pwd': self.apicPassword}}})

        self.__logger.info(f'Login to apic {self.baseUrl}')
        response = self.session.post(self.baseUrl + 'aaaLogin.json', data=userPass, verify=False, timeout=5)

        # Don't rise an exception for 401
        if response.status_code == 401:
            self.__logger.error(f'Login not possible due to Error: {response.text}')
            self.session = False
            return False

        # Raise a exception for all other 4xx and 5xx status_codes
        response.raise_for_status()

        self.token = response.json()['imdata'][0]['aaaLogin']['attributes']['token']
        self.__logger.info('Successful get Token from APIC')
        return True

    # ==============================================================================
    # logout
    # ==============================================================================
    def logout(self):
        self.__logger.debug('Logout from APIC...')
        self.postJson(jsonData={'aaaUser': {'attributes': {'name': self.apicUser}}}, url='aaaLogout.json')

    # ==============================================================================
    # renew cookie (aaaRefresh)
    # ==============================================================================
    def renewCookie(self) -> bool:
        self.__logger.debug('Renew Cookie called')
        response = self.session.post(self.baseUrl + 'aaaRefresh.json', verify=False)

        # Raise Exception for an error 4xx and 5xx
        response.raise_for_status()

        self.token = response.json()['imdata'][0]['aaaLogin']['attributes']['token']
        self.__logger.info('Successful renewed the Token')
        return True

    # ==============================================================================
    # getToken
    # ==============================================================================
    def getToken(self) -> str:
        self.__logger.debug('Get Token called')
        return self.token

    # ==============================================================================
    # getJson
    # ==============================================================================
    def getJson(self, uri, subscription=False) -> {}:
        url = self.baseUrl + uri
        self.__logger.debug(f'Get Json called url: {url}')

        if subscription:
            url = '{}?subscription=yes'.format(url)
        response = self.session.get(url, verify=False)

        if response.ok:
            responseJson = response.json()
            self.__logger.info(f'Successful get Data from APIC: {responseJson}')
            if subscription:
                subscription_id = responseJson['subscriptionId']
                self.__logger.info(f'Returning Subscription Id: {subscription_id}')
                return subscription_id
            return responseJson['imdata']

        elif response.status_code == 400:
            resp_text = response.json()['imdata'][0]['error']['attributes']['text']
            self.__logger.error(f'Error 400 during get occured: {resp_text}')
            if resp_text == 'Unable to process the query, result dataset is too big':
                # Dataset was too big, we try to grab all the data with pagination
                self.__logger.info(f'Trying with Pagination, uri: {uri}')
                return self.getJsonPaged(uri)
            return resp_text
        else:
            self.__logger.error(f'Error during get occured: {response.json()}')
            return response.json()

    # ==============================================================================
    # getJson with Pagination
    # ==============================================================================
    def getJsonPaged(self, uri) -> {}:
        url = self.baseUrl + uri
        self.__logger.debug(f'Get Json Pagination called url: {url}')
        parsed_url = urlparse(url)
        parsed_query = parse_qsl(parsed_url.query)

        return_data = []
        page = 0

        while True:
            parsed_query.extend([('page', page), ('page-size', '50000')])
            page += 1
            url_to_call = urlunparse((parsed_url[0], parsed_url[1], parsed_url[2], parsed_url[3],
                                      urlencode(parsed_query), parsed_url[5]))
            response = self.session.get(url_to_call, verify=False)

            if response.ok:
                responseJson = response.json()
                self.__logger.info(f'Successful get Data from APIC: {responseJson}')
                if responseJson['imdata']:
                    return_data.extend(responseJson['imdata'])
                else:
                    return return_data

            elif response.status_code == 400:
                resp_text = '400: ' + response.json()['imdata'][0]['error']['attributes']['text']
                self.__logger.error(f'Error 400 during get occured: {resp_text}')
                return resp_text

            else:
                self.__logger.error(f'Error during get occured: {response.json()}')
                return False

    # ==============================================================================
    # postJson
    # ==============================================================================
    def postJson(self, jsonData, url='mo.json') -> {}:
        self.__logger.debug(f'Post Json called data: {jsonData}')
        response = self.session.post(self.baseUrl + url, verify=False, data=json.dumps(jsonData, sort_keys=True))
        if response.status_code == 200:
            self.__logger.info(f'Successful Posted Data to APIC: {response.json()}')
            return response.status_code
        elif response.status_code == 400:
            resp_text = '400: ' + response.json()['imdata'][0]['error']['attributes']['text']
            self.__logger.error(f'Error 400 during get occured: {resp_text}')
            return resp_text
        else:
            self.__logger.error(f'Error during get occured: {response.json()}')
            response.raise_for_status()
            return response.status_code

    # ==============================================================================
    # deleteMo
    # ==============================================================================
    def deleteMo(self, dn) -> int:
        self.__logger.debug(f'Delete Mo called DN: {dn}')
        response = self.session.delete(self.baseUrl + "mo/" + dn + ".json", verify=False)

        # Raise Exception if http Error occurred
        response.raise_for_status()

        return response.status_code
