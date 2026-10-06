"""Explicit supervised financial operations. No polling, legacy imports or import-time I/O."""
import hashlib
import json
import os
from datetime import datetime, timezone
from paypal_client import (PayPalClient, ProviderBoundaryError, require_client, evidence_data,
                           identity, cents, request_identity)

def now():return datetime.now(timezone.utc).isoformat()
def canonical(data):return json.dumps(data,sort_keys=True,separators=(',',':'))
def digest(data):return hashlib.sha256(canonical(data).encode()).hexdigest()

def register_obligation(conn,*,obligation_id,task_id,eligibility_decision_id,
                        contract_acceptance_decision_id,payee_id,amount_cents,currency):
    identity(obligation_id);identity(payee_id)
    if isinstance(amount_cents,bool) or not isinstance(amount_cents,int) or amount_cents<=0:
        raise ProviderBoundaryError("Positive integer amount required")
    import re
    if not isinstance(currency,str) or not re.fullmatch('[A-Z]{3}',currency):
        raise ProviderBoundaryError("Explicit currency required")
    fields=dict(obligation_id=obligation_id,task_id=task_id,eligibility_decision_id=eligibility_decision_id,
        contract_acceptance_decision_id=contract_acceptance_decision_id,payee_id=payee_id,
        amount_cents=amount_cents,currency=currency,provider='paypal',environment='PRODUCTION')
    q=conn.execute("SELECT * FROM settlement_eligibility_decisions WHERE decision_id=?",(eligibility_decision_id,)).fetchone()
    if q is None or q['approved']!=1 or any(q[k]!=fields[k] for k in ('task_id','environment','contract_acceptance_decision_id')):
        raise ProviderBoundaryError("Approved matching eligibility required")
    old=conn.execute("SELECT * FROM payment_obligations WHERE obligation_id=?",(obligation_id,)).fetchone()
    if old:
        if any(old[k]!=v for k,v in fields.items()):raise ProviderBoundaryError("Obligation replay conflict")
        return dict(old)
    fields['created_at']=now()
    conn.execute("INSERT INTO payment_obligations ("+','.join(fields)+") VALUES ("+','.join(':'+k for k in fields)+")",fields)
    return fields

def obligation(conn,obligation_id):
    row=conn.execute("SELECT * FROM payment_obligations WHERE obligation_id=?",(obligation_id,)).fetchone()
    if row is None:raise ProviderBoundaryError("Immutable payment obligation required")
    return dict(row)

def bind_order(conn,client,obligation_id,order_id):
    require_client(client,'PRODUCTION')
    o=obligation(conn,obligation_id);order=client.get_order(order_id)
    units=order.get('purchase_units',[])
    if order.get('intent')!='AUTHORIZE' or len(units)!=1:raise ProviderBoundaryError("Order structure mismatch")
    unit=units[0]
    if (unit.get('reference_id')!=obligation_id or unit.get('payee',{}).get('merchant_id')!=o['payee_id']
        or cents(unit.get('amount'))!=(o['amount_cents'],o['currency'])):
        raise ProviderBoundaryError("Order obligation mismatch")
    old=conn.execute("SELECT * FROM payment_order_bindings WHERE obligation_id=?",(obligation_id,)).fetchone()
    if old:
        if old['order_id']!=order_id:raise ProviderBoundaryError("Order replay conflict")
        return dict(old)
    fields=dict(obligation_id=obligation_id,order_id=identity(order_id),
        create_request_id=request_identity('create',obligation_id),
        authorize_request_id=request_identity('authorize',obligation_id),created_at=now())
    conn.execute("INSERT INTO payment_order_bindings VALUES(:obligation_id,:order_id,:create_request_id,:authorize_request_id,:created_at)",fields)
    return fields

def _operation_claim(conn,obligation_id,operation):
    if conn.in_transaction:raise ProviderBoundaryError("Commit obligation/binding before provider operation")
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute("INSERT INTO payment_operation_claims VALUES(?,?,?,?)",
            (obligation_id,operation,request_identity(operation,obligation_id),now()))
        conn.commit()
    except Exception:
        conn.rollback()
        raise ProviderBoundaryError("Operation already claimed; observe instead of retry") from None

def create_provider_order(conn,client,obligation_id,*,approved_obligation_id):
    require_client(client,'PRODUCTION')
    if approved_obligation_id!=obligation_id:raise ProviderBoundaryError("Explicit order approval required")
    o=obligation(conn,obligation_id)
    _operation_claim(conn,obligation_id,'create')
    order_id=client._create_order(o)
    bind_order(conn,client,obligation_id,order_id);conn.commit()
    return order_id

def authorize_provider_order(conn,client,obligation_id,*,approved_obligation_id):
    require_client(client,'PRODUCTION')
    if approved_obligation_id!=obligation_id:raise ProviderBoundaryError("Explicit authorization approval required")
    row=conn.execute("SELECT * FROM payment_order_bindings WHERE obligation_id=?",(obligation_id,)).fetchone()
    if row is None:raise ProviderBoundaryError("Bound order required")
    # Re-observe exact order before the separately approved merchant mutation.
    bind_order(conn,client,obligation_id,row['order_id'])
    _operation_claim(conn,obligation_id,'authorize')
    client._authorize_order(obligation_id,row['order_id'])
    # Retrieve authoritative resources; returned POST status is not caller evidence.
    order=client.get_order(row['order_id'])
    units=order.get('purchase_units',[])
    if len(units)!=1:raise ProviderBoundaryError("Authorization relationship unresolved")
    auths=units[0].get('payments',{}).get('authorizations',[])
    if len(auths)!=1:raise ProviderBoundaryError("Authorization relationship unresolved")
    return client.observe_authorization(row['order_id'],identity(auths[0].get('id')))

def approve_payment_authorization(conn,*,obligation_id,provider_evidence):
    from payment_authority import _establish_payment_authorization
    data=evidence_data(provider_evidence);o=obligation(conn,obligation_id)
    if data['environment']!='PRODUCTION' or data['endpoint']!='https://api-m.paypal.com':
        raise ProviderBoundaryError("Production provider evidence required")
    if any(data[k]!=o[k] for k in ('obligation_id','payee_id','amount_cents','currency','environment')):
        raise ProviderBoundaryError("Provider obligation mismatch")
    bound=conn.execute("SELECT * FROM payment_order_bindings WHERE obligation_id=?",(obligation_id,)).fetchone()
    if bound is None or bound['order_id']!=data['order_id']:
        raise ProviderBoundaryError("Exact order binding required")
    old=conn.execute("SELECT * FROM payment_provider_evidence WHERE obligation_id=?",(obligation_id,)).fetchone()
    if old:
        if (old['provider_authorization_id']!=data['authorization_id'] or old['order_id']!=data['order_id']
            or old['payee_id']!=data['payee_id'] or old['amount_cents']!=data['amount_cents']
            or old['currency']!=data['currency']):
            raise ProviderBoundaryError("Provider evidence replay conflict")
        return dict(conn.execute("SELECT * FROM payment_authorizations WHERE authorization_id=?",(old['payment_authorization_id'],)).fetchone())
    event={'provider':'paypal','provider_event_id':request_identity('evidence',obligation_id),
        'provider_authorization_id':data['authorization_id'],'event_type':'authorization.established',
        'provider_status':'CREATED','amount_cents':data['amount_cents'],'currency':data['currency'],
        'authenticated':True,'raw_payload':data}
    # Savepoint keeps evidence and existing canonical authority writes atomic.
    conn.execute("SAVEPOINT production_payment")
    try:
        result=_establish_payment_authorization(provider_event=event,task_id=o['task_id'],
            contract_acceptance_decision_id=o['contract_acceptance_decision_id'],
            expected_amount_cents=o['amount_cents'],expected_currency=o['currency'],
            expected_provider='paypal',environment='PRODUCTION',_conn=conn,
            _provider_receipt=provider_evidence,_production_obligation=obligation_id)
        auth=dict(result['authorization'])
        conn.execute("INSERT INTO payment_provider_evidence VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ('ppe_'+digest(data),obligation_id,data['order_id'],data['authorization_id'],
             result['event']['event_id'],auth['authorization_id'],'PRODUCTION',data['endpoint'],
             data['payee_id'],data['amount_cents'],data['currency'],canonical(data),digest(data),data['observed_at']))
        conn.execute("RELEASE production_payment")
        return auth
    except Exception:
        conn.execute("ROLLBACK TO production_payment");conn.execute("RELEASE production_payment")
        raise ProviderBoundaryError("Production authorization verification failed") from None

def check_settlement_binding(conn,settlement):
    o=obligation(conn,settlement['obligation_id'])
    fields=('task_id','eligibility_decision_id','contract_acceptance_decision_id','provider','environment','amount_cents','currency')
    if any(o[k]!=settlement[k] for k in fields):raise ProviderBoundaryError("Settlement obligation mismatch")
    p=conn.execute("SELECT * FROM payment_provider_evidence WHERE obligation_id=?",(o['obligation_id'],)).fetchone()
    if p is None or p['payment_authorization_id']!=settlement['payment_authorization_id']:
        raise ProviderBoundaryError("Verified provider binding required")
    return dict(p)

def check_capture_approval(settlement):
    # Protected service configuration, never a public request argument.
    try:
        approval=json.loads(os.environ['CANONICAL_CAPTURE_APPROVAL'])
        fields=('settlement_id','obligation_id','eligibility_decision_id','payment_authorization_id',
                'environment','amount_cents','currency')
        if set(approval)!=set(fields)|{'expires_at'}:raise ValueError()
        if any(type(approval[k]) is not type(settlement[k]) or approval[k]!=settlement[k] for k in fields):
            raise ValueError()
        expires=datetime.fromisoformat(approval['expires_at'])
        remaining=(expires-datetime.now(timezone.utc)).total_seconds()
        if expires.tzinfo is None or not 0 < remaining <= 1800:raise ValueError()
    except Exception:
        raise ProviderBoundaryError("Exact unexpired transaction capture approval required") from None

def validate_capture(provider_evidence,expected):
    from paypal_client import capture_evidence_data
    data=capture_evidence_data(provider_evidence)
    if (data['environment']!='PRODUCTION' or data['endpoint']!='https://api-m.paypal.com'
        or data['provenance_source'] not in ('capture_response','capture_retrieval')
        or data['status']!='COMPLETED' or data['final_capture'] is not True
        or type(data['amount_cents']) is not int
        or data['amount_cents']!=expected['amount_cents'] or data['currency']!=expected['currency']):
        raise ProviderBoundaryError("Capture financial terms unresolved")
    if (data['authorization_id']!=expected['provider_authorization_id']
        or data['order_id']!=expected['order_id']
        or ('payee_id' in data and data['payee_id']!=expected['payee_id'])):
        raise ProviderBoundaryError("Capture resource relationship unresolved")
    # Payee is independently established by the bound order/authorization evidence.
    return {**data,'payee_id':expected['payee_id']}

def preserve_capture(conn,settlement,provider_evidence):
    # Reject caller dictionaries before consulting or writing any canonical state.
    from paypal_client import capture_evidence_data
    capture_evidence_data(provider_evidence)
    from canonical_settlement_engine import get_settlement
    actual=get_settlement(conn,settlement['settlement_id'])
    if actual is None or actual['environment']!='PRODUCTION':
        raise ProviderBoundaryError("Production settlement required")
    expected=check_settlement_binding(conn,actual)
    data=validate_capture(provider_evidence,expected)
    claim=conn.execute("SELECT 1 FROM canonical_capture_attempts WHERE settlement_id=?",
                       (actual['settlement_id'],)).fetchone()
    if claim is None or actual['state'] not in ('CAPTURE_REQUESTED','RECONCILING','PROVIDER_CONFIRMED','REVENUE_RECORDED'):
        raise ProviderBoundaryError("Existing capture claim and prepared/reconciling settlement required")
    old=conn.execute("SELECT * FROM payment_capture_evidence WHERE settlement_id=?",(actual['settlement_id'],)).fetchone()
    if old:
        if old['capture_id']!=data['capture_id']:raise ProviderBoundaryError("Capture evidence conflict")
        check_confirmed_capture(conn,actual,data['capture_id'])
        return
    conn.execute("INSERT INTO payment_capture_evidence VALUES(?,?,?,?,?,?,?,?)",
        (actual['settlement_id'],data['capture_id'],'PRODUCTION',data['endpoint'],canonical(data),digest(data),
         data['observed_at'],data['provenance_source']))

def observe_capture(conn,client,settlement):
    require_client(client,'PRODUCTION');expected=check_settlement_binding(conn,settlement)
    auth=client.get_authorization(expected['provider_authorization_id']);order=client.get_order(expected['order_id'])
    up=client.endpoint+'/v2/checkout/orders/'+expected['order_id']
    if not any(x.get('rel')=='up' and x.get('href')==up for x in auth.get('links',[])):
        raise ProviderBoundaryError("Reconciliation order link mismatch")
    units=order.get('purchase_units',[])
    if order.get('intent')!='AUTHORIZE' or len(units)!=1:raise ProviderBoundaryError("Reconciliation order mismatch")
    unit=units[0];auths=unit.get('payments',{}).get('authorizations',[])
    if (unit.get('reference_id')!=settlement['obligation_id'] or unit.get('payee',{}).get('merchant_id')!=expected['payee_id']
        or cents(unit.get('amount'))!=(expected['amount_cents'],expected['currency'])
        or cents(auth.get('amount'))!=(expected['amount_cents'],expected['currency'])
        or len(auths)!=1 or auths[0].get('id')!=expected['provider_authorization_id']
        or cents(auths[0].get('amount'))!=(expected['amount_cents'],expected['currency'])):
        raise ProviderBoundaryError("Reconciliation financial binding mismatch")
    captures=unit.get('payments',{}).get('captures',[])
    if not captures:
        return {'outcome':'NOT_CAPTURED'} if auth.get('status')=='CREATED' else {'outcome':'UNRESOLVED'}
    if len(captures)!=1 or captures[0].get('status')!='COMPLETED':return {'outcome':'UNRESOLVED'}
    payload=client.get_capture(identity(captures[0].get('id')))
    data=validate_capture(payload,expected);preserve_capture(conn,settlement,payload)
    return {'outcome':'COMPLETED','capture_id':data['capture_id']}

def capture_once(conn,*,settlement_id,client):
    from paypal_capture_service import prepare_capture,execute_capture
    require_client(client,'PRODUCTION')
    if conn.in_transaction:raise ProviderBoundaryError("Commit settlement before capture")
    prepare_capture(conn,settlement_id);conn.commit()
    result=execute_capture(conn,settlement_id,provider_client=client);conn.commit()
    return result


def check_confirmed_capture(conn,settlement,capture_id):
    expected=check_settlement_binding(conn,settlement)
    row=conn.execute("SELECT * FROM payment_capture_evidence WHERE settlement_id=?",(settlement['settlement_id'],)).fetchone()
    if row is None or row['capture_id']!=capture_id:
        raise ProviderBoundaryError("Authoritative immutable capture proof required")
    data=json.loads(row['observation_json'])
    if (row['provenance_source'] not in ('capture_response','capture_retrieval')
        or data.get('provenance_source')!=row['provenance_source']
        or data.get('status')!='COMPLETED' or data.get('final_capture') is not True
        or digest(data)!=row['observation_sha256'] or data['capture_id']!=capture_id
        or any(data[k]!=expected[k] for k in ('order_id','payee_id','amount_cents','currency','environment','endpoint'))
        or data['authorization_id']!=expected['provider_authorization_id']):
        raise ProviderBoundaryError("Capture proof binding mismatch")

def committed_capture_permit(conn,settlement,request_id,*,claim_receipt=None):
    from paypal_client import _issue_capture_permit,_consume_claim_receipt
    from paypal_capture_service import _check_capture_gate,build_capture_request_id
    if request_id!=build_capture_request_id(settlement['settlement_id']):
        raise ProviderBoundaryError("Deterministic capture identity required")
    _consume_claim_receipt(claim_receipt,settlement['settlement_id'],request_id)
    fresh=conn.execute("SELECT state FROM canonical_settlements WHERE settlement_id=?",(settlement['settlement_id'],)).fetchone()
    if fresh is None or fresh[0]!='CAPTURE_REQUESTED':
        raise ProviderBoundaryError("Prepared settlement required")
    if conn.in_transaction:
        raise ProviderBoundaryError("Durable capture claim required")
    expected=check_settlement_binding(conn,settlement)
    claim=conn.execute("SELECT request_id FROM canonical_capture_attempts WHERE settlement_id=?",
                       (settlement['settlement_id'],)).fetchone()
    if claim is None or claim[0]!=request_id:
        raise ProviderBoundaryError("Exact durable capture claim required")
    _check_capture_gate(settlement)
    return _issue_capture_permit(dict(settlement=settlement,
        authorization_id=expected['provider_authorization_id'],request_id=request_id))
