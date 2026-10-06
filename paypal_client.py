"""Fixed-environment PayPal transport. No network occurs at import or construction."""
import os
import re
import hashlib
import json
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from types import MappingProxyType
import weakref
import requests

ENDPOINTS = MappingProxyType({'SANDBOX':'https://api-m.sandbox.paypal.com',
                             'PRODUCTION':'https://api-m.paypal.com'})
class ProviderBoundaryError(ValueError):
    pass

class ProviderObservationUnavailable(ProviderBoundaryError):
    """No authoritative observation was obtained; a later GET may resume."""
    pass

def identity(value):
    if not isinstance(value,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}',value):
        raise ProviderBoundaryError("Invalid provider resource identity")
    return value

def cents(amount):
    try:
        value=Decimal(amount['value'])
        currency=amount['currency_code']
        if not value.is_finite() or value <= 0 or value*100 != (value*100).to_integral_value():
            raise ValueError()
        if not re.fullmatch('[A-Z]{3}',currency): raise ValueError()
        return int(value*100),currency
    except (KeyError,TypeError,ValueError,InvalidOperation):
        raise ProviderBoundaryError("Invalid provider amount") from None

def request_identity(operation, obligation_id):
    identity(obligation_id)
    return 'mp-'+hashlib.sha256((operation+':'+obligation_id).encode()).hexdigest()[:32]

# Opaque receipts are meaningful only inside the trusted financial service process.
# They are not a defence against arbitrary code execution in that process.
_RECEIPTS = {}
class ProviderEvidence:
    __slots__ = ('__weakref__',)
    def __new__(cls,*args,**kwargs):
        raise ProviderBoundaryError("Provider evidence cannot be caller-constructed")

def _receipt(data):
    import weakref
    receipt=object.__new__(ProviderEvidence)
    if not isinstance(_RECEIPTS,weakref.WeakKeyDictionary):
        globals()['_RECEIPTS']=weakref.WeakKeyDictionary()
    _RECEIPTS[receipt]=json.dumps(data,sort_keys=True,separators=(',',':'))
    return receipt

def evidence_data(receipt):
    if type(receipt) is not ProviderEvidence or receipt not in _RECEIPTS:
        raise ProviderBoundaryError("Trusted provider receipt required")
    return json.loads(_RECEIPTS[receipt])

_CAPTURE_EVIDENCE=weakref.WeakKeyDictionary()
class CaptureEvidence:
    __slots__=('__weakref__',)
    def __new__(cls,*args,**kwargs):
        raise ProviderBoundaryError("Capture evidence cannot be caller-constructed")

def _capture_evidence(payload,environment,endpoint,source):
    # Called only after the fixed-environment transport returned a resource.
    amount,currency=cents(payload.get('amount'))
    related=payload.get('supplementary_data',{}).get('related_ids',{})
    status=payload.get('status')
    if not isinstance(status,str) or not re.fullmatch('[A-Z_]+',status):
        raise ProviderBoundaryError("Malformed capture status")
    final=payload.get('final_capture')
    if type(final) is not bool:raise ProviderBoundaryError("Malformed final-capture evidence")
    data=dict(capture_id=identity(payload.get('id')),
        authorization_id=identity(related.get('authorization_id')),order_id=identity(related.get('order_id')),
        amount_cents=amount,currency=currency,status=status,final_capture=final,
        environment=environment,endpoint=endpoint,provenance_source=source,
        observed_at=datetime.now(timezone.utc).isoformat())
    if 'payee' in payload:data['payee_id']=identity(payload['payee'].get('merchant_id'))
    receipt=object.__new__(CaptureEvidence)
    _CAPTURE_EVIDENCE[receipt]=json.dumps(data,sort_keys=True,separators=(',',':'))
    return receipt

def capture_evidence_data(receipt):
    if type(receipt) is not CaptureEvidence or receipt not in _CAPTURE_EVIDENCE:
        raise ProviderBoundaryError("Trusted environment-bound capture receipt required")
    return json.loads(_CAPTURE_EVIDENCE[receipt])

_CLAIM_RECEIPTS=weakref.WeakKeyDictionary()
class CaptureClaimReceipt:
    __slots__=('__weakref__',)
    def __new__(cls,*args,**kwargs):
        raise ProviderBoundaryError("Capture claim receipt cannot be caller-constructed")

def _new_claim_receipt(settlement_id,request_id):
    receipt=object.__new__(CaptureClaimReceipt)
    _CLAIM_RECEIPTS[receipt]=(settlement_id,request_id)
    return receipt

def _consume_claim_receipt(receipt,settlement_id,request_id):
    if type(receipt) is not CaptureClaimReceipt or receipt not in _CLAIM_RECEIPTS:
        raise ProviderBoundaryError("Fresh single-use capture claim receipt required")
    if _CLAIM_RECEIPTS.pop(receipt)!=(settlement_id,request_id):
        raise ProviderBoundaryError("Capture claim receipt mismatch")

_CAPTURE_PERMITS=weakref.WeakKeyDictionary()
class CapturePermit:
    __slots__=('__weakref__',)
    def __new__(cls,*args,**kwargs):
        raise ProviderBoundaryError("Capture permit cannot be caller-constructed")

def _issue_capture_permit(data):
    permit=object.__new__(CapturePermit)
    _CAPTURE_PERMITS[permit]=json.dumps(data,sort_keys=True,separators=(',',':'))
    return permit

def _consume_capture_permit(permit):
    if type(permit) is not CapturePermit or permit not in _CAPTURE_PERMITS:
        raise ProviderBoundaryError("Single-use committed capture permit required")
    return json.loads(_CAPTURE_PERMITS.pop(permit))

class PayPalClient:
    __slots__=('_environment',)
    def __init__(self, environment):
        if environment not in ENDPOINTS:
            raise ProviderBoundaryError("Explicit provider environment required")
        object.__setattr__(self,'_environment',environment)
    def __setattr__(self,name,value):
        raise ProviderBoundaryError("Provider client environment is immutable")
    @property
    def environment(self): return self._environment
    @property
    def endpoint(self): return ENDPOINTS[self.environment]
    def _credentials(self):
        prefix='PAYPAL_'+self.environment+'_'
        values=(os.getenv(prefix+'CLIENT_ID'),os.getenv(prefix+'CLIENT_SECRET'))
        if not all(isinstance(v,str) and v and v==v.strip() for v in values):
            raise ProviderBoundaryError("Dedicated provider credentials unavailable")
        # Reject an accidentally copied Sandbox pair in the Production source.
        if self.environment=='PRODUCTION' and values == (
                os.getenv('PAYPAL_SANDBOX_CLIENT_ID'),os.getenv('PAYPAL_SANDBOX_CLIENT_SECRET')):
            raise ProviderBoundaryError("Cross-environment credentials forbidden")
        return values
    def _request(self,method,path,*,body=None,request_id=None,_before_dispatch=None):
        observation=method=='GET'
        try:
            with requests.Session() as session:
                session.trust_env=False
                try:
                    credentials=self._credentials()
                except ProviderBoundaryError:
                    if observation:raise ProviderObservationUnavailable("Provider observation unavailable") from None
                    raise
                token_response=session.post(self.endpoint+'/v1/oauth2/token',
                    auth=credentials,data={'grant_type':'client_credentials'},timeout=15,allow_redirects=False)
                if token_response.status_code!=200:
                    if observation:raise ProviderObservationUnavailable("Provider observation unavailable")
                    raise ProviderBoundaryError("Provider authentication failed")
                try:
                    token=token_response.json().get('access_token')
                except Exception:
                    if observation:raise ProviderObservationUnavailable("Provider observation unavailable") from None
                    raise ProviderBoundaryError("Provider authentication failed") from None
                if not isinstance(token,str) or not token:
                    if observation:raise ProviderObservationUnavailable("Provider observation unavailable")
                    raise ProviderBoundaryError("Provider authentication failed")
                headers={'Authorization':'Bearer '+token,'Accept':'application/json',
                         'Content-Type':'application/json','Prefer':'return=representation'}
                if request_id:headers['PayPal-Request-Id']=identity(request_id)
                if _before_dispatch is not None:_before_dispatch()
                response=session.request(method,self.endpoint+path,json=body,headers=headers,
                                         timeout=15,allow_redirects=False)
                if response.status_code not in (200,201):
                    if observation:raise ProviderObservationUnavailable("Provider observation unavailable")
                    raise ProviderBoundaryError("Provider operation failed")
                data=response.json()
                if not isinstance(data,dict):raise ProviderBoundaryError("Invalid provider response")
                return data
        except ProviderObservationUnavailable:
            raise ProviderObservationUnavailable("Provider observation unavailable") from None
        except (requests.exceptions.RequestException,TimeoutError,ConnectionError,OSError):
            if observation:raise ProviderObservationUnavailable("Provider observation unavailable") from None
            raise ProviderBoundaryError("Provider request failed") from None
        except Exception:
            raise ProviderBoundaryError("Provider request failed") from None
    def get_authorization(self,authorization_id):
        authorization_id=identity(authorization_id)
        data=self._request('GET','/v2/payments/authorizations/'+authorization_id)
        if data.get('id')!=authorization_id:raise ProviderBoundaryError("Authorization identity mismatch")
        return data
    def get_order(self,order_id):
        order_id=identity(order_id)
        data=self._request('GET','/v2/checkout/orders/'+order_id)
        if data.get('id')!=order_id:raise ProviderBoundaryError("Order identity mismatch")
        return data
    def _create_order(self,obligation):
        order=self._request('POST','/v2/checkout/orders',request_id=request_identity('create',obligation['obligation_id']),
            body={'intent':'AUTHORIZE','purchase_units':[{'reference_id':obligation['obligation_id'],
                'payee':{'merchant_id':obligation['payee_id']},'amount':{
                'value':f"{obligation['amount_cents']//100}.{obligation['amount_cents']%100:02d}",
                'currency_code':obligation['currency']}}]})
        identity(order.get('id'))
        return order['id']
    def get_approval_url(self,order_id):
        from urllib.parse import urlparse,parse_qs
        order=self.get_order(order_id)
        host='www.paypal.com' if self.environment=='PRODUCTION' else 'www.sandbox.paypal.com'
        for link in order.get('links',[]):
            if link.get('rel') not in ('approve','payer-action'):continue
            url=link.get('href','');parsed=urlparse(url)
            if (parsed.scheme=='https' and parsed.netloc==host and not parsed.fragment
                and parsed.path.rstrip('/')=='/checkoutnow'
                and parse_qs(parsed.query).get('token')==[order_id]):
                return url
        raise ProviderBoundaryError("Expected environment-bound customer approval URL unavailable")
    def _authorize_order(self,obligation_id,order_id):
        return self._request('POST','/v2/checkout/orders/'+identity(order_id)+'/authorize',body={},
            request_id=request_identity('authorize',obligation_id))
    def observe_authorization(self,order_id,authorization_id):
        order=self.get_order(order_id);auth=self.get_authorization(authorization_id)
        units=order.get('purchase_units')
        if order.get('intent')!='AUTHORIZE' or not isinstance(units,list) or len(units)!=1:
            raise ProviderBoundaryError("Single AUTHORIZE purchase unit required")
        unit=units[0];authorizations=unit.get('payments',{}).get('authorizations',[])
        if len(authorizations)!=1 or authorizations[0].get('id')!=authorization_id:
            raise ProviderBoundaryError("Order authorization relationship mismatch")
        links=auth.get('links',[])
        expected=self.endpoint+'/v2/checkout/orders/'+order_id
        if not any(z.get('rel')=='up' and z.get('href')==expected for z in links):
            raise ProviderBoundaryError("Provider environment/order link mismatch")
        amount,currency=cents(auth.get('amount'))
        if cents(unit.get('amount'))!=(amount,currency) or cents(authorizations[0].get('amount'))!=(amount,currency):
            raise ProviderBoundaryError("Provider financial terms mismatch")
        if auth.get('status')!='CREATED' or authorizations[0].get('status')!='CREATED':
            raise ProviderBoundaryError("Authorization is not eligible")
        if unit.get('payments',{}).get('captures'):
            raise ProviderBoundaryError("Authorization already has capture evidence")
        data={'environment':self.environment,'endpoint':self.endpoint,'order_id':identity(order_id),
              'authorization_id':identity(authorization_id),'payee_id':identity(unit.get('payee',{}).get('merchant_id')),
              'obligation_id':identity(unit.get('reference_id')),'amount_cents':amount,'currency':currency,
              'status':'CREATED','observed_at':datetime.now(timezone.utc).isoformat()}
        return _receipt(data)
    def capture_authorization(self,authorization_id,*,request_id,amount_cents,currency,permit=None):
        before_dispatch=None
        if self.environment=='PRODUCTION':
            proof=_consume_capture_permit(permit)
            if (proof['authorization_id']!=authorization_id or proof['request_id']!=request_id
                or proof['settlement']['amount_cents']!=amount_cents or proof['settlement']['currency']!=currency
                or proof['settlement']['environment']!='PRODUCTION'):
                raise ProviderBoundaryError("Capture permit resource mismatch")
            from paypal_capture_service import _check_capture_gate
            before_dispatch=lambda:_check_capture_gate(proof['settlement'])
        payload=self._request('POST','/v2/payments/authorizations/'+identity(authorization_id)+'/capture',
            request_id=request_id,body={'amount':{'value':f"{amount_cents//100}.{amount_cents%100:02d}",
                                                  'currency_code':currency},'final_capture':True},_before_dispatch=before_dispatch)
        if self.environment=='PRODUCTION':
            return _capture_evidence(payload,self.environment,self.endpoint,'capture_response')
        return payload
    def get_capture(self,capture_id):
        capture_id=identity(capture_id)
        data=self._request('GET','/v2/payments/captures/'+capture_id)
        if data.get('id')!=capture_id:raise ProviderBoundaryError("Capture identity mismatch")
        if self.environment=='PRODUCTION':
            return _capture_evidence(data,self.environment,self.endpoint,'capture_retrieval')
        return data

def require_client(client,environment):
    if type(client) is not PayPalClient or client.environment!=environment:
        raise ProviderBoundaryError("Environment-bound client required")
