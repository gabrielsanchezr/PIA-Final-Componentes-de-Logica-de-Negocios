from fastapi import FastAPI, Depends, HTTPException, Query, Header
from sqlalchemy.orm import Session
from sqlalchemy import func
from datetime import datetime, timedelta, date
from decimal import Decimal
from uuid import uuid4
from .database import Base, engine, get_db, SessionLocal
from . import models as m
from .schemas import *
from .domain import *
from .seed import seed_fixtures

Base.metadata.create_all(bind=engine)
if engine.dialect.name == 'sqlite':
    from sqlalchemy import text
    with engine.begin() as _conn:
        _conn.execute(text("CREATE TRIGGER IF NOT EXISTS trg_audit_no_update BEFORE UPDATE ON bitacora_auditoria BEGIN SELECT RAISE(ABORT, 'audit log is append-only'); END"))
        _conn.execute(text("CREATE TRIGGER IF NOT EXISTS trg_audit_no_delete BEFORE DELETE ON bitacora_auditoria BEGIN SELECT RAISE(ABORT, 'audit log is append-only'); END"))
with SessionLocal() as _db: seed_fixtures(_db)
app=FastAPI(title='AutoFix Pro API',version='1.0.0',description='API de gestión de órdenes de servicio, cotizaciones, capacidad, pagos y auditoría de AutoFix Pro.')

def audit(db,folio,user,role,action,entity,detail=None):
    db.add(m.AuditLog(folio=folio,usuario=user,rol=role,accion=action,entidad=entity,detalle=detail or {}))
def event(db,folio,name,payload=None): db.add(m.DomainEventRecord(folio=folio,nombre=name,payload=payload or {}))
def order_or_404(db,folio):
    o=db.get(m.ServiceOrder,folio)
    if not o: raise HTTPException(404,'Orden no encontrada')
    return o
def order_total(db,folio):
    return money(sum((Decimal(str(x.precio_unitario))*x.cantidad for x in db.query(m.OrderLine).filter_by(folio=folio).all()),Decimal('0')))
def total_due(db,folio):
    quote=db.query(m.Quote).filter_by(folio=folio,estado='APROBADA').order_by(m.Quote.enviado_at.desc()).first()
    base=Decimal(str(quote.total)) if quote else Decimal('0')
    extras = sum(
        (
            Decimal(str(x.monto))
            for x in db.query(m.AdditionalWork).filter(
                m.AdditionalWork.folio == folio,
                m.AdditionalWork.estado.in_(['APROBADO', 'AUTORIZADO_ASESOR'])
            ).all()
        ),
        Decimal('0')
    )
    order=db.get(m.ServiceOrder,folio)
    storage=Decimal('0')
    if order and order.fecha_listo and order.fecha_resguardo:
        days=max(0,(datetime.utcnow()-order.fecha_listo).days-15); storage=Decimal(days)*Decimal('100')
    return money(base+extras+storage)
def quote_total(q): return money(sum((Decimal(str(x.precio_unitario))*x.cantidad for x in q.lineas),Decimal('0')))
def actor_dict(a): return {'actor_id':a.actor_id,'nombre':a.nombre,'es_preferente':a.es_preferente,'activo':a.activo,'ciudad':a.ciudad}
def operation_dict(o): return {'operacion_id':o.operacion_id,'actor_id':o.actor_id,'estado_inicial':o.estado_inicial,'fecha_solicitud':o.fecha_solicitud,'promocion_activa':o.promocion_activa,'autorizado':o.autorizado,'referencia':o.referencia}

@app.get('/',include_in_schema=False)
def home():
    from fastapi.responses import RedirectResponse
    return RedirectResponse('/app')

@app.get('/app',include_in_schema=False)
def frontend():
    from fastapi.responses import HTMLResponse
    return HTMLResponse(PAGINA_HTML)
@app.get('/api/health')
def health(): return {'sistema':'AutoFix Pro','estado':'activo','base_de_datos':'configurada'}
@app.get('/api/db-health')
def db_health(db:Session=Depends(get_db)):
    try: db.execute(func.count().select() if False else __import__('sqlalchemy').text('SELECT 1'))
    except Exception as e: raise HTTPException(503,str(e))
    return {'base_de_datos':'SQLite' if engine.dialect.name=='sqlite' else engine.dialect.name,'estado':'conectada','mensaje':'Conexión exitosa con AutoFix Pro'}

# Legacy fixture read API and repositories
@app.get('/api/actores')
def actors(ciudad:str|None=None,activo:bool|None=None,es_preferente:bool|None=None,pagina:int=Query(1,ge=1),limite:int=Query(10,ge=1,le=100),db:Session=Depends(get_db)):
    q=db.query(m.Actor)
    if ciudad:q=q.filter(m.Actor.ciudad==ciudad)
    if activo is not None:q=q.filter(m.Actor.activo==activo)
    if es_preferente is not None:q=q.filter(m.Actor.es_preferente==es_preferente)
    total=q.count(); rows=q.order_by(m.Actor.actor_id).offset((pagina-1)*limite).limit(limite).all()
    return {'total':total,'pagina':pagina,'limite':limite,'resultados':[actor_dict(x) for x in rows]}
@app.get('/api/actores/{actor_id}')
def actor(actor_id:str,db:Session=Depends(get_db)):
    x=db.get(m.Actor,actor_id)
    if not x: raise HTTPException(404,'Actor no encontrado')
    return actor_dict(x)
@app.get('/api/catalogo')
def catalog(db:Session=Depends(get_db)):
    return [{'recurso_id':x.recurso_id,'descripcion':x.descripcion,'precio_mxn':x.precio_mxn,'capacidad_o_stock':x.capacidad_o_stock,'habilitado':x.habilitado} for x in db.query(m.CatalogItem).order_by(m.CatalogItem.recurso_id)]
@app.get('/api/operaciones')
def operations(actor_id:str|None=None,estado:str|None=None,pagina:int=Query(1,ge=1),limite:int=Query(10,ge=1,le=100),db:Session=Depends(get_db)):
    q=db.query(m.Operation)
    if actor_id:q=q.filter_by(actor_id=actor_id)
    if estado:q=q.filter(m.Operation.estado_inicial==estado)
    total=q.count(); rows=q.order_by(m.Operation.operacion_id).offset((pagina-1)*limite).limit(limite).all()
    return {'total':total,'pagina':pagina,'limite':limite,'resultados':[operation_dict(x) for x in rows]}
@app.get('/api/operaciones/{operacion_id}')
def operation(operacion_id:str,db:Session=Depends(get_db)):
    x=db.get(m.Operation,operacion_id)
    if not x: raise HTTPException(404,'Operación no encontrada')
    return operation_dict(x)
@app.get('/api/detalles')
def details(operacion_id:str|None=None,db:Session=Depends(get_db)):
    q=db.query(m.OperationDetail)
    if operacion_id:q=q.filter_by(operacion_id=operacion_id)
    return [{'operacion_id':x.operacion_id,'recurso_id':x.recurso_id,'cantidad':x.cantidad} for x in q.all()]
@app.get('/api/casos-prueba')
def cases(operacion_id:str|None=None,db:Session=Depends(get_db)):
    q=db.query(m.TestCase)
    if operacion_id:q=q.filter_by(operacion_id=operacion_id)
    return [{'caso_id':x.caso_id,'operacion_id':x.operacion_id,'tipo_prueba':x.tipo_prueba,'regla_a_verificar':x.regla_a_verificar,'resultado_alumno':x.resultado_alumno,'instrucciones':x.instrucciones} for x in q.all()]
@app.get('/api/transiciones-prueba')
def transitions_fixture(operacion_id:str|None=None,db:Session=Depends(get_db)):
    q=db.query(m.TransitionFixture)
    if operacion_id:q=q.filter_by(operacion_id=operacion_id)
    return [{'caso_id':x.caso_id,'operacion_id':x.operacion_id,'accion_sugerida':x.accion_sugerida,'estado_inicial':x.estado_inicial,'resultado_alumno':x.resultado_alumno,'instrucciones':x.instrucciones} for x in q.all()]

# US-01, US-11, US-15, US-16: reception and physical capacity
@app.post('/api/ordenes',status_code=201)
def create_order(data:ReceptionCreate,usuario:str=Header('sistema'),rol:str=Header('ASESOR'),db:Session=Depends(get_db)):
    a=db.get(m.Actor,data.actor_id)
    if not a or not a.activo: raise HTTPException(422,'El propietario debe existir y estar activo')
    if bool(data.aseguradora) != bool(data.poliza): raise HTTPException(422,'Si se captura aseguradora, también se requiere póliza; si no aplica, ambos campos deben omitirse')
    occupied_bays=db.query(m.ServiceOrder).filter(m.ServiceOrder.bahia==True,m.ServiceOrder.estado.notin_(['CERRADA','CANCELADA'])).count()
    occupied_slots=db.query(m.ServiceOrder).filter(m.ServiceOrder.cajon==True,m.ServiceOrder.estado.notin_(['CERRADA','CANCELADA'])).count()
    if (occupied_bays>=12 and occupied_slots>=8) and not data.flotilla_contrato:
        event(db,None,'CapacidadSaturada',{'bahias':occupied_bays,'cajones':occupied_slots}); db.commit()
        raise HTTPException(409,'Capacidad saturada: máximo 12 bahías y 8 cajones. Solo flotillas con contrato tienen excepción.')
    folio='AF-'+datetime.utcnow().strftime('%Y%m%d')+'-'+uuid4().hex[:6].upper()
    o=m.ServiceOrder(folio=folio,actor_id=data.actor_id,nombre_cliente=data.nombre_cliente,telefono=data.telefono,correo=data.correo,placas=data.placas.upper(),vin=data.vin.upper(),aseguradora=data.aseguradora,poliza=data.poliza,flotilla_contrato=data.flotilla_contrato,fecha_estimada_entrega=data.fecha_estimada_entrega,tecnico=data.tecnico,bahia=occupied_bays<12,cajon=occupied_slots<8)
    db.add(o); db.flush()
    for typ,url in data.evidencias.items(): db.add(m.PhotoEvidence(folio=folio,tipo=typ,url=url,etapa='RECEPCION'))
    audit(db,folio,usuario,rol,'ORDEN_CREADA','OrdenDeServicio',{'placas':o.placas,'vin':o.vin}); event(db,folio,'OrdenCreada',{'folio':folio})
    db.commit(); return {'folio':folio,'estado':o.estado,'fecha_apertura':o.fecha_apertura,'bahia_asignada':o.bahia,'cajon_asignado':o.cajon}
@app.get('/api/ordenes/{folio}')
def get_order(folio:str,db:Session=Depends(get_db)):
    o=order_or_404(db,folio)
    return {'folio':o.folio,'actor_id':o.actor_id,'cliente':o.nombre_cliente,'telefono':o.telefono,'placas':o.placas,'vin':o.vin,'estado':o.estado,'fecha_apertura':o.fecha_apertura,'tecnico':o.tecnico,'total':order_total(db,folio)}
@app.get('/api/ordenes/{folio}/evidencias')
def evidence(folio:str,db:Session=Depends(get_db)):
    order_or_404(db,folio)
    return [{'tipo':x.tipo,'url':x.url,'etapa':x.etapa,'created_at':x.created_at} for x in db.query(m.PhotoEvidence).filter_by(folio=folio).all()]
@app.post('/api/ordenes/{folio}/evidencias',status_code=201)
def add_evidence(folio:str,tipo:str,url:str,etapa:str='REPARACION',usuario:str=Header('sistema'),db:Session=Depends(get_db)):
    order_or_404(db,folio); x=m.PhotoEvidence(folio=folio,tipo=tipo,url=url,etapa=etapa); db.add(x); audit(db,folio,usuario,'TECNICO','EVIDENCIA_AGREGADA','PhotoEvidence',{'tipo':tipo,'etapa':etapa}); db.commit(); return {'id':x.id,'registrada':True}
# Diagnosis, quotations and customer authorization
@app.post('/api/ordenes/{folio}/cotizaciones',status_code=201)
def create_quote(folio:str,data:QuoteCreate,usuario:str=Header('asesor'),db:Session=Depends(get_db)):
    o=order_or_404(db,folio)
    if o.estado not in {'DIAGNOSTICADA','ESPERANDO_AUTORIZACION'}:
        raise HTTPException(409,'Primero debe registrarse el diagnóstico de la unidad')
    total=quote_total(data)
    qid='COT-'+uuid4().hex[:8].upper()
    dep=required_deposit(total) if deposit_required(total) else Decimal('0.00')
    q=m.Quote(folio_cotizacion=qid,folio=folio,total=total,canal=data.canal.upper(),anticipo_requerido=dep)
    db.add(q)
    for line in data.lineas:
        db.add(m.OrderLine(folio=folio,descripcion=line.descripcion,categoria=line.categoria.upper(),cantidad=line.cantidad,precio_unitario=money(line.precio_unitario)))
    if o.estado=='DIAGNOSTICADA':
        validate_transition(o.estado,'ESPERANDO_AUTORIZACION')
    o.estado='ESPERANDO_AUTORIZACION'
    audit(db,folio,usuario,'ASESOR','COTIZACION_ENVIADA','Cotizacion',{'cotizacion':qid,'total':str(total)})
    event(db,folio,'CotizacionEnviada',{'cotizacion':qid,'canal':data.canal})
    db.add(m.Notification(folio=folio,canal=data.canal.upper(),mensaje=f'Cotización {qid} por ${total} MXN enviada (registro simulado; requiere integración externa para envío real).'))
    db.commit()
    return {'cotizacion_id':qid,'folio':folio,'total_mxn':total,'anticipo_requerido':dep,'estado':q.estado,'notificacion':'registrada; envío externo no configurado'}

@app.get('/api/ordenes/{folio}/cotizaciones')
def list_quotes(folio:str,db:Session=Depends(get_db)):
    order_or_404(db,folio)
    return [{'cotizacion_id':q.folio_cotizacion,'total':q.total,'estado':q.estado,'anticipo_requerido':q.anticipo_requerido,'anticipo_pagado':q.anticipo_pagado,'canal':q.canal} for q in db.query(m.Quote).filter_by(folio=folio).all()]

@app.post('/api/cotizaciones/{cotizacion_id}/decision')
def quote_decision(cotizacion_id:str,data:QuoteDecision,db:Session=Depends(get_db)):
    q=db.get(m.Quote,cotizacion_id)
    if not q:
        raise HTTPException(404,'Cotización no encontrada')
    if q.estado!='PENDIENTE':
        raise HTTPException(409,'La cotización ya fue resuelta')
    o=order_or_404(db,q.folio)
    q.estado='APROBADA' if data.aprobado else 'RECHAZADA'
    q.autorizado_por=data.usuario
    q.firma=data.firma
    audit(db,q.folio,data.usuario,'CLIENTE','COTIZACION_'+q.estado,'Cotizacion',{'cotizacion':cotizacion_id,'firma':data.firma})
    event(db,q.folio,'CotizacionAutorizada' if data.aprobado else 'CotizacionRechazada',{'cotizacion':cotizacion_id})
    if not data.aprobado:
        o.estado='CANCELADA'
    db.commit()
    return {'cotizacion_id':cotizacion_id,'estado':q.estado,'anticipo_requerido':q.anticipo_requerido}

@app.post('/api/ordenes/{folio}/pagos',status_code=201)
def add_payment(folio:str,data:PaymentCreate,usuario:str=Header('caja'),db:Session=Depends(get_db)):
    o=order_or_404(db,folio)
    amount=money(data.monto)
    approved=db.query(m.Quote).filter_by(folio=folio,estado='APROBADA').order_by(m.Quote.enviado_at.desc()).first()
    if not approved:
        raise HTTPException(409,'No hay cotización aprobada')
    due=total_due(db,folio)
    if Decimal(str(o.monto_pagado))+amount>due:
        raise HTTPException(422,f'El pago excede el saldo pendiente. Total a pagar: {due}')
    p=m.Payment(folio=folio,monto=amount,tipo=data.tipo.upper(),referencia=data.referencia)
    db.add(p)
    o.monto_pagado=money(Decimal(str(o.monto_pagado))+amount)
    if data.tipo.upper()=='ANTICIPO':
        approved.anticipo_pagado=money(Decimal(str(approved.anticipo_pagado))+amount)
    if o.monto_pagado>=total_due(db,folio):
        o.pago_total=True
    audit(db,folio,usuario,'CAJERO','PAGO_REGISTRADO','Pago',{'monto':str(amount),'tipo':data.tipo})
    db.commit()
    return {'pago_id':p.id,'monto_pagado':o.monto_pagado,'pago_total':o.pago_total}

# State machine, technician assignment and closing
@app.post('/api/ordenes/{folio}/transiciones')
def transition(folio:str,data:TransitionRequest,db:Session=Depends(get_db)):
    o=order_or_404(db,folio)
    target=data.estado_destino.upper()
    try:
        validate_transition(o.estado,target)
    except DomainError as e:
        event(db,folio,'TransicionRechazada',{'actual':o.estado,'destino':target})
        audit(db,folio,data.usuario,data.rol,'TRANSICION_RECHAZADA','OrdenDeServicio',{'destino':target})
        db.commit()
        raise HTTPException(409,str(e))
    if target=='EN_REPARACION':
        q=db.query(m.Quote).filter_by(folio=folio,estado='APROBADA').order_by(m.Quote.enviado_at.desc()).first()
        if not q:
            raise HTTPException(409,'Se requiere cotización aprobada')
        if q.anticipo_requerido>0 and q.anticipo_pagado<q.anticipo_requerido:
            raise HTTPException(409,'Anticipo del 50% pendiente')
    previous_state=o.estado
    o.estado=target
    if target=='LISTA_PARA_ENTREGA':
        o.vehiculo_reparado=True
        o.fecha_listo=datetime.utcnow()
        event(db,folio,'OrdenListaParaEntrega')
    audit(db,folio,data.usuario,data.rol,'ESTADO_CAMBIADO','OrdenDeServicio',{'estado_anterior':previous_state,'estado_nuevo':target})
    event(db,folio,'EstadoOrdenActualizado',{'estado':target})
    db.commit()
    return {'folio':folio,'estado':target}

@app.post('/api/ordenes/{folio}/aceptacion-tecnico')
def technician_accept(folio:str,tecnico:str,usuario:str=Header('tecnico'),db:Session=Depends(get_db)):
    o=order_or_404(db,folio)
    o.tecnico=tecnico
    o.tecnico_acepto=True
    audit(db,folio,usuario,'TECNICO','ASIGNACION_ACEPTADA','OrdenDeServicio',{'tecnico':tecnico})
    db.commit()
    return {'folio':folio,'tecnico':tecnico,'aceptada':True}

@app.post('/api/ordenes/{folio}/cerrar')
def close_order(folio:str,data:CloseRequest,db:Session=Depends(get_db)):
    o=order_or_404(db,folio)
    if o.estado not in {'LISTA_PARA_ENTREGA','EN_RES_GUARDO'}:
        raise HTTPException(409,'El vehículo debe estar listo para entrega')
    o.pago_total=Decimal(str(o.monto_pagado))>=total_due(db,folio)
    cond=CierreTriple(o.vehiculo_reparado,o.pago_total,bool(data.firma_conformidad.strip()))
    if not cond.is_satisfied():
        raise HTTPException(409,'No se cumple la condición triple: vehículo reparado, pago total y firma de conformidad')
    o.firma_conformidad=data.firma_conformidad
    o.estado='CERRADA'
    o.bahia=False
    o.cajon=False
    audit(db,folio,data.usuario,'CAJERO','ORDEN_CERRADA','OrdenDeServicio',{'pago_total':True,'firma':True})
    event(db,folio,'OrdenCerrada')
    event(db,None,'EspacioLiberado',{'folio':folio})
    db.commit()
    return {'folio':folio,'estado':o.estado,'cerrada':True}

# CU-03 additional work and deviations
@app.post('/api/ordenes/{folio}/trabajos-adicionales',status_code=201)
def additional(folio:str,data:AdditionalCreate,db:Session=Depends(get_db)):
    o=order_or_404(db,folio)
    amount=money(data.monto)
    original=db.query(m.Quote).filter_by(folio=folio).order_by(m.Quote.enviado_at.asc()).first()
    if not original:
        raise HTTPException(409,'Primero debe existir una cotización original')

    acumulados = db.query(m.AdditionalWork).filter(
        m.AdditionalWork.folio == folio,
        m.AdditionalWork.estado.in_(['APROBADO', 'AUTORIZADO_ASESOR'])
    ).all()

    accumulated = money(
        sum((Decimal(str(x.monto)) for x in acumulados), Decimal('0')) + amount
    )

    exceeds=deviation_requires_management(original.total,accumulated)
    needs_client=additional_needs_customer(amount)
    status='PENDIENTE_APROBACIONES' if (exceeds or needs_client) else 'AUTORIZADO_ASESOR'

    x=m.AdditionalWork(
        folio=folio,
        descripcion=data.descripcion,
        monto=amount,
        autorizado_por=data.asesor if status=='AUTORIZADO_ASESOR' else None,
        estado=status,
        requiere_cliente=needs_client,
        requiere_gerencia=exceeds,
        cliente_aprobado=not needs_client,
        gerencia_aprobada=not exceeds
    )
    db.add(x)
    audit(db,folio,data.asesor,'ASESOR','TRABAJO_ADICIONAL_REGISTRADO','TrabajoAdicional',{'monto':str(amount),'estado':status,'requiere_cliente':needs_client,'requiere_gerencia':exceeds})
    event(db,folio,'TrabajoAdicionalDetectado',{'monto':str(amount)})
    db.commit()
    return {'id':x.id,'estado':status,'requiere_aprobacion_cliente':needs_client,'requiere_autorizacion_gerencia':exceeds}

@app.post('/api/trabajos-adicionales/{work_id}/decision')
def additional_decision(work_id:int,data:AdditionalDecision,db:Session=Depends(get_db)):
    x=db.get(m.AdditionalWork,work_id)
    if not x:
        raise HTTPException(404,'Trabajo adicional no encontrado')
    if not x.requiere_cliente or x.cliente_aprobado:
        raise HTTPException(409,'El trabajo no está pendiente de aprobación del cliente')
    if not data.aprobado:
        x.estado='RECHAZADO'
    else:
        x.cliente_aprobado=True
        x.estado='APROBADO' if x.gerencia_aprobada else 'PENDIENTE_APROBACIONES'
    audit(db,x.folio,data.usuario,'CLIENTE','TRABAJO_ADICIONAL_'+x.estado,'TrabajoAdicional',{'id':work_id})
    db.commit()
    return {'id':work_id,'estado':x.estado}

@app.post('/api/ordenes/{folio}/incremento-refaccion')
def price_increase(folio:str,data:PriceIncreaseRequest,db:Session=Depends(get_db)):
    order_or_404(db,folio)
    old=money(data.precio_original)
    new=money(data.precio_nuevo)
    pct=(new-old)/old*100
    if pct>=10:
        x=m.PriceAdjustment(folio=folio,precio_original=old,precio_nuevo=new,porcentaje=pct,motivo=data.motivo,solicitado_por=data.usuario)
        db.add(x)
        audit(db,folio,data.usuario,'ASESOR','INCREMENTO_PRECIO_PENDIENTE','Refaccion',{'porcentaje':str(pct),'motivo':data.motivo})
        db.commit()
        return {'id':x.id,'requiere_autorizacion_gerencia':True,'incremento_porcentaje':pct,'estado':x.estado}
    audit(db,folio,data.usuario,'ASESOR','INCREMENTO_PRECIO_AUTORIZADO','Refaccion',{'porcentaje':str(pct),'motivo':data.motivo})
    db.commit()
    return {'requiere_autorizacion_gerencia':False,'incremento_porcentaje':pct,'estado':'AUTORIZADO_ASESOR'}

@app.post('/api/trabajos-adicionales/{work_id}/autorizacion-gerencia')
def manager_additional_decision(work_id:int,aprobado:bool,gerente:str,db:Session=Depends(get_db)):
    x=db.get(m.AdditionalWork,work_id)
    if not x:
        raise HTTPException(404,'Trabajo adicional no encontrado')
    if not x.requiere_gerencia or x.gerencia_aprobada:
        raise HTTPException(409,'No requiere autorización pendiente de Gerencia')
    if not aprobado:
        x.estado='RECHAZADO'
    else:
        x.gerencia_aprobada=True
        x.estado='APROBADO' if x.cliente_aprobado else 'PENDIENTE_APROBACIONES'
    x.autorizado_por=gerente
    audit(db,x.folio,gerente,'GERENCIA','DESVIACION_'+x.estado,'TrabajoAdicional',{'id':work_id})
    db.commit()
    return {'id':work_id,'estado':x.estado}

@app.post('/api/ajustes-precio/{adjustment_id}/decision')
def price_adjustment_decision(adjustment_id:int,aprobado:bool,gerente:str,db:Session=Depends(get_db)):
    x=db.get(m.PriceAdjustment,adjustment_id)
    if not x:
        raise HTTPException(404,'Ajuste de precio no encontrado')
    if x.estado!='PENDIENTE_GERENCIA':
        raise HTTPException(409,'El ajuste ya fue resuelto')
    x.estado='APROBADO' if aprobado else 'RECHAZADO'
    x.autorizado_por=gerente
    audit(db,x.folio,gerente,'GERENCIA','AJUSTE_PRECIO_'+x.estado,'Refaccion',{'id':adjustment_id,'porcentaje':str(x.porcentaje)})
    db.commit()
    return {'id':adjustment_id,'estado':x.estado}

@app.post('/api/ordenes/{folio}/evidencias-pieza',status_code=201)
def add_part_replacement_evidence(folio:str,antes_url:str,despues_url:str,usuario:str,db:Session=Depends(get_db)):
    order_or_404(db,folio)
    if not antes_url.strip() or not despues_url.strip():
        raise HTTPException(422,'Se requieren evidencias antes y después de sustituir la pieza')
    before=m.PhotoEvidence(folio=folio,tipo='ANTES_SUSTITUCION',url=antes_url,etapa='SUSTITUCION')
    after=m.PhotoEvidence(folio=folio,tipo='DESPUES_SUSTITUCION',url=despues_url,etapa='SUSTITUCION')
    db.add_all([before,after])
    audit(db,folio,usuario,'TECNICO','EVIDENCIA_SUSTITUCION_REGISTRADA','PhotoEvidence',{'antes':antes_url,'despues':despues_url})
    db.commit()
    return {'folio':folio,'evidencias_registradas':2}

@app.post('/api/ordenes/{folio}/diagnostico')
def register_diagnosis(folio:str,diagnostico:str,tecnico:str,db:Session=Depends(get_db)):
    o=order_or_404(db,folio)
    try:
        validate_transition(o.estado,'DIAGNOSTICADA')
    except DomainError as e:
        raise HTTPException(409,str(e))
    old=o.estado
    o.estado='DIAGNOSTICADA'
    audit(db,folio,tecnico,'TECNICO','DIAGNOSTICO_REGISTRADO','OrdenDeServicio',{'diagnostico':diagnostico,'estado_anterior':old})
    event(db,folio,'OrdenDiagnosticada',{'diagnostico':diagnostico})
    db.commit()
    return {'folio':folio,'estado':o.estado,'diagnostico':diagnostico}

@app.post('/api/ordenes/{folio}/deslinde-seguridad')
def safety_disclaimer(folio:str,riesgo:str,cliente:str,firma:str,db:Session=Depends(get_db)):
    order_or_404(db,folio)
    if not firma.strip():
        raise HTTPException(422,'Se requiere firma digital de deslinde')
    audit(db,folio,cliente,'CLIENTE','DESLINDE_SEGURIDAD_FIRMADO','OrdenDeServicio',{'riesgo_rechazado':riesgo,'firma':firma})
    event(db,folio,'DeslindeFirmado',{'riesgo':riesgo})
    db.commit()
    return {'folio':folio,'deslinde_registrado':True,'garantia_afectada':True}
# Parts supply SLA and out-of-stock flow
@app.post('/api/ordenes/{folio}/refacciones',status_code=201)
def create_part(folio:str,data:PartCreate,db:Session=Depends(get_db)):
    order_or_404(db,folio)
    try:
        deadline=part_deadline(data.proveedor_tipo.upper())
    except DomainError as e:
        raise HTTPException(422,str(e))
    x=m.PartOrder(folio=folio,descripcion=data.descripcion,proveedor_tipo=data.proveedor_tipo.upper(),fecha_limite=deadline)
    db.add(x)
    audit(db,folio,'compras','COMPRAS','REFACCION_SOLICITADA','Refaccion',{'proveedor':data.proveedor_tipo,'fecha_limite':deadline.isoformat()})
    db.commit()
    return {'id':x.id,'fecha_limite':deadline,'estado':x.estado}

@app.patch('/api/refacciones/{part_id}')
def update_part(part_id:int,data:PartUpdate,db:Session=Depends(get_db)):
    x=db.get(m.PartOrder,part_id)
    if not x:
        raise HTTPException(404,'Pedido de refacción no encontrado')
    x.estado=data.estado.upper()
    if x.estado=='AGOTADA':
        x.agotada_desde=datetime.utcnow()
    audit(db,x.folio,'compras','COMPRAS','ESTADO_REFACCION_CAMBIADO','Refaccion',{'estado':x.estado})
    db.commit()
    return {'id':x.id,'estado':x.estado}

@app.post('/api/refacciones/{part_id}/buscar-alternos')
def search_alternatives(part_id:int,alternativas:list[str],usuario:str,db:Session=Depends(get_db)):
    x=db.get(m.PartOrder,part_id)
    if not x:
        raise HTTPException(404,'Pedido de refacción no encontrado')
    if not alternativas:
        raise HTTPException(422,'Registra al menos una alternativa consultada')
    x.estado='ALTERNOS_BUSCADOS'
    audit(db,x.folio,usuario,'COMPRAS','ALTERNOS_BUSCADOS','Refaccion',{'part_id':part_id,'alternativas':alternativas})
    db.commit()
    return {'id':part_id,'estado':x.estado,'alternativas_consultadas':len(alternativas)}

@app.post('/api/refacciones/{part_id}/pieza-cliente')
def customer_part(part_id:int,acepta_sin_garantia:bool,usuario:str,db:Session=Depends(get_db)):
    x=db.get(m.PartOrder,part_id)
    if not x:
        raise HTTPException(404,'Pedido no encontrado')
    if not acepta_sin_garantia:
        raise HTTPException(409,'Debe aceptarse explícitamente que la pieza aportada por cliente no tiene garantía de componente')
    x.aportada_cliente=True
    x.sin_garantia=True
    x.estado='APORTADA_CLIENTE'
    audit(db,x.folio,usuario,'CLIENTE','PIEZA_CLIENTE_SIN_GARANTIA','Refaccion',{'part_id':part_id})
    db.commit()
    return {'id':part_id,'aportada_cliente':True,'sin_garantia':True}

@app.get('/api/refacciones/en-riesgo')
def parts_at_risk(db:Session=Depends(get_db)):
    now=datetime.utcnow()
    rows=db.query(m.PartOrder).filter(m.PartOrder.estado.notin_(['RECIBIDA','CANCELADA'])).all()
    return [{'id':x.id,'folio':x.folio,'descripcion':x.descripcion,'estado':'EN_RIESGO' if now>x.fecha_limite else x.estado,'fecha_limite':x.fecha_limite,'escalamiento_gerencia':now>x.fecha_limite or (x.agotada_desde and now>x.agotada_desde+timedelta(hours=48))} for x in rows]

# Delay notifications, storage and customer portal/history
@app.post('/api/ordenes/{folio}/retraso')
def delay(folio:str,data:DelayRequest,db:Session=Depends(get_db)):
    o=order_or_404(db,folio)
    o.fecha_estimada_entrega=data.fecha_estimada_entrega
    if data.horas_retraso>4:
        msg=f'Retraso proyectado de {data.horas_retraso} horas. Nueva fecha estimada: {data.fecha_estimada_entrega.isoformat()}'
        db.add(m.Notification(folio=folio,canal='WHATSAPP',mensaje=msg))
        audit(db,folio,data.usuario,'ASESOR','RETRASO_NOTIFICADO','OrdenDeServicio',{'horas':data.horas_retraso})
    db.commit()
    return {'notificacion_requerida':data.horas_retraso>4,'registro':'creado' if data.horas_retraso>4 else 'no requerido'}

@app.post('/api/ordenes/{folio}/llamadas-recoleccion')
def storage_calls(folio:str,data:StorageRequest,db:Session=Depends(get_db)):
    o=order_or_404(db,folio)
    o.llamadas_recoleccion=data.llamadas_recoleccion
    days=(datetime.utcnow()-o.fecha_listo).days if o.fecha_listo else 0
    if days>=15 and data.llamadas_recoleccion>=3 and o.estado=='LISTA_PARA_ENTREGA':
        o.estado='EN_RES_GUARDO'
        o.fecha_resguardo=datetime.utcnow()
        event(db,folio,'OrdenEnResguardo')
        audit(db,folio,'sistema','CAJA','ORDEN_EN_RESGUARDO','OrdenDeServicio',{'dias':days,'llamadas':data.llamadas_recoleccion})
    db.commit()
    return {'folio':folio,'estado':o.estado,'dias_desde_listo':days}

@app.post('/api/ordenes/{folio}/calcular-almacenaje')
def storage_fee(folio:str,fecha:datetime|None=None,db:Session=Depends(get_db)):
    o=order_or_404(db,folio)
    asof=fecha or datetime.utcnow()
    if not o.fecha_listo:
        return {'dias_cobrables':0,'cargo_mxn':Decimal('0.00')}
    days=max(0,(asof-o.fecha_listo).days-15)
    fee=money(Decimal(days)*Decimal('100'))
    return {'dias_cobrables':days,'cargo_mxn':fee,'moneda':'MXN'}

@app.get('/api/portal/{folio}')
def customer_portal(folio:str,db:Session=Depends(get_db)):
    o=order_or_404(db,folio)
    return {'folio':o.folio,'placas':o.placas,'estado':o.estado,'fecha_apertura':o.fecha_apertura,'fecha_estimada_entrega':o.fecha_estimada_entrega}

@app.get('/api/vehiculos/{vin}/historial')
def vehicle_history(vin:str,db:Session=Depends(get_db)):
    orders=db.query(m.ServiceOrder).filter_by(vin=vin.upper()).order_by(m.ServiceOrder.fecha_apertura.desc()).all()
    return [{'folio':o.folio,'estado':o.estado,'fecha_apertura':o.fecha_apertura,'lineas':[{'descripcion':l.descripcion,'categoria':l.categoria,'cantidad':l.cantidad,'precio_unitario':l.precio_unitario} for l in o.lines],'garantias':[{'descripcion':w.descripcion,'vigente_hasta':w.vigente_hasta} for w in db.query(m.Warranty).filter(m.Warranty.vin==vin.upper(),m.Warranty.vigente_hasta>=date.today()).all()],'recomendaciones_pendientes':[{'id':r.id,'descripcion':r.descripcion} for r in db.query(m.Recommendation).filter_by(vin=vin.upper(),atendida=False).all()]} for o in orders]

@app.post('/api/vehiculos/{vin}/garantias',status_code=201)
def add_warranty(vin:str,descripcion:str,vigente_hasta:date,usuario:str,db:Session=Depends(get_db)):
    x=m.Warranty(vin=vin.upper(),descripcion=descripcion,vigente_hasta=vigente_hasta)
    db.add(x)
    audit(db,None,usuario,'ASESOR','GARANTIA_REGISTRADA','Garantia',{'vin':vin,'descripcion':descripcion})
    db.commit()
    return {'id':x.id,'vigente_hasta':x.vigente_hasta}

@app.post('/api/vehiculos/{vin}/recomendaciones',status_code=201)
def add_recommendation(vin:str,descripcion:str,usuario:str,db:Session=Depends(get_db)):
    x=m.Recommendation(vin=vin.upper(),descripcion=descripcion)
    db.add(x)
    audit(db,None,usuario,'TECNICO','RECOMENDACION_REGISTRADA','Recomendacion',{'vin':vin,'descripcion':descripcion})
    db.commit()
    return {'id':x.id,'registrada':True}

@app.patch('/api/recomendaciones/{recommendation_id}/atender')
def resolve_recommendation(recommendation_id:int,usuario:str,db:Session=Depends(get_db)):
    x=db.get(m.Recommendation,recommendation_id)
    if not x:
        raise HTTPException(404,'Recomendación no encontrada')
    x.atendida=True
    audit(db,None,usuario,'TECNICO','RECOMENDACION_ATENDIDA','Recomendacion',{'id':recommendation_id})
    db.commit()
    return {'id':x.id,'atendida':True}

@app.get('/api/ordenes/{folio}/auditoria')
def audit_for_order(folio:str,db:Session=Depends(get_db)):
    order_or_404(db,folio)
    return [{'id':x.id,'usuario':x.usuario,'rol':x.rol,'accion':x.accion,'entidad':x.entidad,'detalle':x.detalle,'fecha':x.fecha} for x in db.query(m.AuditLog).filter_by(folio=folio).order_by(m.AuditLog.fecha).all()]

@app.get('/api/auditoria')
def audit_all(db:Session=Depends(get_db)):
    return [{'id':x.id,'folio':x.folio,'usuario':x.usuario,'rol':x.rol,'accion':x.accion,'entidad':x.entidad,'detalle':x.detalle,'fecha':x.fecha} for x in db.query(m.AuditLog).order_by(m.AuditLog.fecha.desc()).limit(500).all()]

@app.get('/api/eventos')
def events(db:Session=Depends(get_db)):
    return [{'id':x.id,'folio':x.folio,'nombre':x.nombre,'payload':x.payload,'occurred_at':x.occurred_at} for x in db.query(m.DomainEventRecord).order_by(m.DomainEventRecord.id.desc()).limit(500).all()]

@app.get('/api/kpis/diarios')
def daily_kpis(fecha:date|None=None,db:Session=Depends(get_db)):
    day=fecha or date.today()
    orders=db.query(m.ServiceOrder).filter(func.date(m.ServiceOrder.fecha_apertura)==day.isoformat()).all()
    closed=[o for o in orders if o.estado=='CERRADA']
    open_orders=[o for o in orders if o.estado not in {'CERRADA','CANCELADA'}]
    revenue=db.query(func.coalesce(func.sum(m.Payment.monto),0)).filter(func.date(m.Payment.created_at)==day.isoformat()).scalar()
    avg_hours=None
    durations=[(o.fecha_listo-o.fecha_apertura).total_seconds()/3600 for o in closed if o.fecha_listo]
    if durations:
        avg_hours=round(sum(durations)/len(durations),2)
    bay_count=db.query(m.ServiceOrder).filter(m.ServiceOrder.bahia==True,m.ServiceOrder.estado.notin_(['CERRADA','CANCELADA'])).count()
    slot_count=db.query(m.ServiceOrder).filter(m.ServiceOrder.cajon==True,m.ServiceOrder.estado.notin_(['CERRADA','CANCELADA'])).count()
    delayed=[o for o in orders if o.fecha_estimada_entrega and o.fecha_estimada_entrega.date()<day and o.estado not in {'CERRADA','CANCELADA'}]
    delay_pct=round((len(delayed)/len(orders))*100,2) if orders else 0.0
    by_tech={}
    for o in closed:
        if o.tecnico:
            by_tech[o.tecnico]=by_tech.get(o.tecnico,0)+1
    category_revenue={}
    for o in orders:
        for line in o.lines:
            category_revenue[line.categoria]=category_revenue.get(line.categoria,Decimal('0'))+Decimal(str(line.precio_unitario))*line.cantidad
    return {'fecha':day,'ordenes_abiertas':len(open_orders),'ordenes_cerradas':len(closed),'ingresos_mxn':money(revenue),'ingresos_por_categoria_mxn':{k:money(v) for k,v in category_revenue.items()},'tiempo_promedio_reparacion_horas':avg_hours,'ocupacion_bahias':f'{bay_count}/12','ocupacion_cajones':f'{slot_count}/8','porcentaje_retrasos':delay_pct,'productividad_por_tecnico':by_tech}

# Audit log is append-only at API level; deny destructive methods explicitly.
@app.put('/api/auditoria/{entry_id}',include_in_schema=False)
def immutable_audit_put(entry_id:int):
    raise HTTPException(405,'La bitácora de auditoría es inmutable (US-19)')

@app.patch('/api/auditoria/{entry_id}',include_in_schema=False)
def immutable_audit_patch(entry_id:int):
    raise HTTPException(405,'La bitácora de auditoría es inmutable (US-19)')

@app.delete('/api/auditoria/{entry_id}',include_in_schema=False)
def immutable_audit_delete(entry_id:int):
    raise HTTPException(405,'La bitácora de auditoría es inmutable (US-19)')


# Pantalla web de AutoFix Pro (se sirve en / y /app)
PAGINA_HTML = r"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>AutoFix Pro · Operación del taller</title>
<style>
:root{--dark:#1B2430;--dark2:#2B3A4B;--orange:#F26B1D;--green:#2E9E6B;--red:#D64545;--blue:#3A7CA5;--amber:#E8A317;--bg:#F1F4F7;--card:#fff;--line:#D9DFE6;--muted:#6B7787;font-family:Segoe UI,Calibri,Arial,sans-serif}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--dark);font-size:15px}
header{background:var(--dark);color:#fff;padding:12px 24px;display:flex;flex-wrap:wrap;gap:16px;align-items:center}
.brand{font-size:20px;font-weight:700;display:flex;align-items:center;gap:10px}
.logo{width:34px;height:34px;border-radius:50%;background:var(--orange);display:inline-flex;align-items:center;justify-content:center;font-size:18px}
nav{display:flex;gap:4px;flex-wrap:wrap;margin-left:12px}
nav a{color:#C9D1DB;text-decoration:none;padding:8px 14px;border-radius:8px;font-weight:600}
nav a.on,nav a:hover{background:var(--dark2);color:#fff}
.who{margin-left:auto;display:flex;gap:8px;align-items:center;flex-wrap:wrap}
.who label{font-size:12px;color:#C9D1DB}
.who select,.who input{padding:6px 8px;border-radius:6px;border:1px solid #455468;background:#fff;color:var(--dark);width:130px}
.who a{color:#F2B84B;font-size:13px}
main{max-width:1180px;margin:0 auto;padding:22px 24px 60px}
h1{font-size:26px;margin:4px 0 4px}
h2{font-size:18px;margin:0 0 12px}
.sub{color:var(--muted);margin:0 0 18px}
.grid{display:grid;gap:16px}
.g2{grid-template-columns:repeat(auto-fit,minmax(340px,1fr))}
.g4{grid-template-columns:repeat(auto-fit,minmax(200px,1fr))}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:18px}
.card.dim{opacity:.62}
.tag{display:inline-block;font-size:11px;font-weight:700;padding:2px 8px;border-radius:99px;background:var(--bg);color:var(--muted);margin-left:8px;vertical-align:middle}
.stat{text-align:center}
.stat b{display:block;font-size:34px;color:var(--orange)}
.stat span{color:var(--muted);font-size:13px}
label.f{display:block;font-size:12px;font-weight:700;color:var(--muted);margin:10px 0 3px}
input,select,textarea{width:100%;padding:9px 10px;border:1px solid var(--line);border-radius:8px;font:inherit;background:#fff}
textarea{min-height:64px;resize:vertical}
.row{display:flex;gap:10px;flex-wrap:wrap}
.row>*{flex:1;min-width:120px}
button{font:inherit;font-weight:700;border:0;border-radius:8px;padding:9px 16px;cursor:pointer;background:var(--orange);color:#fff}
button.sec{background:var(--dark2)}
button.ok{background:var(--green)}
button.bad{background:var(--red)}
button.ghost{background:#E8EDF2;color:var(--dark)}
button:disabled{opacity:.5;cursor:wait}
.actions{display:flex;gap:8px;margin-top:14px;flex-wrap:wrap}
table{width:100%;border-collapse:collapse;font-size:14px}
th{text-align:left;font-size:12px;color:var(--muted);padding:8px;border-bottom:2px solid var(--line)}
td{padding:9px 8px;border-bottom:1px solid var(--line);vertical-align:top}
td:first-child{white-space:nowrap}
tr.link{cursor:pointer}
tr.link:hover{background:#F7F9FB}
.scroll{overflow-x:auto}
.badge{display:inline-block;padding:3px 10px;border-radius:99px;font-size:12px;font-weight:700;color:#fff;background:var(--blue)}
.b-RECIBIDA{background:#5B6B7F}.b-DIAGNOSTICADA{background:var(--blue)}.b-ESPERANDO_AUTORIZACION{background:var(--amber)}
.b-EN_REPARACION{background:var(--orange)}.b-ESPERANDO_REFACCION{background:var(--amber)}.b-LISTA_PARA_ENTREGA{background:var(--green)}
.b-CERRADA{background:#1F7A50}.b-CANCELADA{background:var(--red)}.b-EN_RES_GUARDO{background:#7A5BB5}
.b-PENDIENTE,.b-PENDIENTE_APROBACIONES,.b-SOLICITADA{background:var(--amber)}.b-APROBADA,.b-APROBADO,.b-RECIBIDA_OK{background:var(--green)}.b-RECHAZADA,.b-RECHAZADO,.b-AGOTADA{background:var(--red)}
.steps{display:flex;gap:6px;flex-wrap:wrap;margin:14px 0}
.step{flex:1;min-width:120px;text-align:center;padding:9px 6px;border-radius:8px;background:#E8EDF2;color:var(--muted);font-size:12px;font-weight:700}
.step.done{background:#CFEBDD;color:#1F7A50}.step.now{background:var(--orange);color:#fff}
.next{background:#FFF4EA;border:1px solid #F8C9A5;border-radius:10px;padding:12px 16px;margin:6px 0 16px}
.bar{height:14px;background:#E8EDF2;border-radius:99px;overflow:hidden;margin:6px 0 4px}
.bar i{display:block;height:100%;background:var(--green)}.bar i.hi{background:var(--red)}
.line{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-bottom:10px;align-items:center;padding-bottom:10px;border-bottom:1px dashed var(--line)}
.line .ld{grid-column:1/-1}
.line button{padding:8px 11px}
.kv{display:grid;grid-template-columns:130px 1fr;gap:4px 10px;font-size:14px}
.kv dt{color:var(--muted)}.kv dd{margin:0;font-weight:600}
.msg{padding:10px 14px;border-radius:8px;margin-top:12px;font-size:14px}
.msg.good{background:#E3F4EB;border:1px solid #A9DBC2}.msg.warn{background:#FCE9E9;border:1px solid #F0B7B7}
.mini{border:1px dashed var(--line);border-radius:8px;padding:10px;margin-top:10px;font-size:14px}
.mini .row{align-items:center}
.small{font-size:13px;color:var(--muted)}
#toast{position:fixed;right:20px;bottom:20px;display:flex;flex-direction:column;gap:8px;z-index:9}
.t{background:var(--dark);color:#fff;padding:12px 16px;border-radius:10px;max-width:380px;box-shadow:0 6px 20px #0003;border-left:6px solid var(--green)}
.t.err{border-left-color:var(--red)}
.empty{color:var(--muted);padding:14px 0}
@media(max-width:700px){.who{margin-left:0}}
</style>
</head>
<body>
<header>
  <div class="brand"><span class="logo">&#128295;</span>AutoFix Pro</div>
  <nav id="nav">
    <a href="#/inicio" data-r="inicio">Inicio</a>
    <a href="#/recepcion" data-r="recepcion">Recepción</a>
    <a href="#/ordenes" data-r="ordenes">Órdenes</a>
    <a href="#/consultas" data-r="consultas">Consultas</a>
    <a href="#/auditoria" data-r="auditoria">Auditoría</a>
  </nav>
  <div class="who">
    <div><label for="role">Rol</label><br><select id="role"></select></div>
    <div><label for="user">Usuario</label><br><input id="user" maxlength="30"></div>
    <a href="/docs" target="_blank" rel="noopener">Swagger</a>
  </div>
</header>
<main id="view"><p class="sub">Cargando…</p></main>
<div id="toast"></div>
<noscript>Esta pantalla necesita JavaScript.</noscript>
<script>
"use strict";
const ROLES = {ASESOR:'asesor1', TECNICO:'tecnico1', CAJERO:'caja1', GERENTE:'gerente1', CLIENTE:'cliente1'};
const ESTADOS = ['RECIBIDA','DIAGNOSTICADA','ESPERANDO_AUTORIZACION','EN_REPARACION','LISTA_PARA_ENTREGA','CERRADA'];
const TRANSICIONES = {
  RECIBIDA:['DIAGNOSTICADA','CANCELADA'],
  DIAGNOSTICADA:['ESPERANDO_AUTORIZACION','EN_REPARACION','CANCELADA'],
  ESPERANDO_AUTORIZACION:['EN_REPARACION','CANCELADA'],
  EN_REPARACION:['ESPERANDO_REFACCION','LISTA_PARA_ENTREGA','CANCELADA'],
  ESPERANDO_REFACCION:['EN_REPARACION','CANCELADA'],
  LISTA_PARA_ENTREGA:['EN_RES_GUARDO','CERRADA'],
  EN_RES_GUARDO:['CERRADA'], CANCELADA:[], CERRADA:[]
};
const SIGUIENTE = {
  RECIBIDA:['TECNICO','Registrar el diagnóstico de la unidad.'],
  DIAGNOSTICADA:['ASESOR','Crear la cotización y enviarla al cliente.'],
  ESPERANDO_AUTORIZACION:['CLIENTE','El cliente aprueba la cotización; si pasa de $5,000, el cajero registra el anticipo del 50%.'],
  EN_REPARACION:['TECNICO','Terminar la reparación y cambiar el estado a LISTA_PARA_ENTREGA.'],
  ESPERANDO_REFACCION:['TECNICO','Esperar la refacción y regresar a EN_REPARACION cuando llegue.'],
  LISTA_PARA_ENTREGA:['CAJERO','Registrar el pago total y cerrar la orden con la firma de conformidad.'],
  EN_RES_GUARDO:['CAJERO','Vehículo en resguardo: registrar el pago y cerrar la orden.'],
  CERRADA:['','La orden está cerrada. No admite más cambios de estado.'],
  CANCELADA:['','La orden fue cancelada.']
};
const $ = (s, r=document) => r.querySelector(s);
const $$ = (s, r=document) => [...r.querySelectorAll(s)];
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const MXN = new Intl.NumberFormat('es-MX',{style:'currency',currency:'MXN'});
const money = n => MXN.format(Number(n||0));
const fdate = s => s ? new Date(s.endsWith('Z')||/[+-]\d\d:\d\d$/.test(s)?s:s+'Z').toLocaleString('es-MX',{dateStyle:'medium',timeStyle:'short'}) : '—';
const fshort = s => s ? new Date(s.endsWith('Z')||/[+-]\d\d:\d\d$/.test(s)?s:s+'Z').toLocaleString('es-MX',{dateStyle:'short',timeStyle:'short'}) : '—';
const badge = e => `<span class="badge b-${esc(e)}">${esc(String(e||'—').replace(/_/g,' '))}</span>`;
const ascii = s => String(s||'').normalize('NFD').replace(/[\u0300-\u036f]/g,'').replace(/[^\x20-\x7E]/g,'').trim() || 'usuario';
const role = () => localStorage.getItem('af_role') || 'ASESOR';
const user = () => localStorage.getItem('af_user') || ROLES[role()];

function toast(msg, err){
  const d = document.createElement('div'); d.className = 't' + (err?' err':''); d.textContent = msg;
  $('#toast').appendChild(d); setTimeout(()=>d.remove(), err?7000:4000);
}
function fmtErr(data, status){
  const d = data && data.detail;
  if(typeof d === 'string') return d;
  if(Array.isArray(d)) return d.map(e => ((e.loc||[]).filter(x=>x!=='body'&&x!=='query').join('.')||'dato') + ': ' + e.msg).join(' · ');
  return 'Error ' + status;
}
async function api(method, path, o={}){
  const url = new URL(path, location.origin);
  Object.entries(o.query||{}).forEach(([k,v]) => { if(v!==''&&v!=null) url.searchParams.set(k,v); });
  const headers = {usuario: ascii(user()), rol: role()};
  if(o.body) headers['Content-Type'] = 'application/json';
  const res = await fetch(url, {method, headers, body: o.body ? JSON.stringify(o.body) : undefined});
  let data = null; try{ data = await res.json(); }catch(_){}
  if(!res.ok){ const e = new Error(fmtErr(data,res.status)); e.status = res.status; throw e; }
  return data;
}
async function act(btn, fn, okMsg){
  if(btn) btn.disabled = true;
  try{ const r = await fn(); if(okMsg) toast(typeof okMsg==='function'?okMsg(r):okMsg); return r; }
  catch(e){ toast(e.message, true); return null; }
  finally{ if(btn) btn.disabled = false; }
}
const val = (id, r=document) => (r.querySelector('#'+id)||{}).value ?? '';
function kvs(obj){ return '<dl class="kv">' + obj.map(([k,v]) => `<dt>${esc(k)}</dt><dd>${v}</dd>`).join('') + '</dl>'; }
function table(cols, rows, rowAttr){
  if(!rows.length) return '<div class="empty">Sin registros.</div>';
  return '<div class="scroll"><table><thead><tr>' + cols.map(c=>`<th>${esc(c)}</th>`).join('') + '</tr></thead><tbody>' +
    rows.map(r => `<tr ${rowAttr?rowAttr(r):''}>${r.cells.map(c=>`<td>${c}</td>`).join('')}</tr>`).join('') + '</tbody></table></div>';
}
const view = () => $('#view');

/* ---------- Inicio ---------- */
async function vInicio(){
  const [k, risk] = await Promise.all([api('GET','/api/kpis/diarios'), api('GET','/api/refacciones/en-riesgo')]);
  const occ = s => { const [a,b] = String(s||'0/1').split('/').map(Number); return {a,b,p:Math.round(100*a/(b||1))}; };
  const bay = occ(k.ocupacion_bahias), slot = occ(k.ocupacion_cajones);
  const bar = (o,l) => `<div><b>${l}</b>: ${o.a} de ${o.b} ocupados<div class="bar"><i class="${o.p>=90?'hi':''}" style="width:${o.p}%"></i></div></div>`;
  view().innerHTML = `
  <h1>Resumen del día</h1><p class="sub">Indicadores operativos del taller (${esc(k.fecha)}).</p>
  <div class="grid g4">
    <div class="card stat"><b>${k.ordenes_abiertas}</b><span>órdenes abiertas</span></div>
    <div class="card stat"><b>${k.ordenes_cerradas}</b><span>órdenes cerradas</span></div>
    <div class="card stat"><b>${money(k.ingresos_mxn)}</b><span>ingresos del día</span></div>
    <div class="card stat"><b>${Number(k.porcentaje_retrasos||0).toFixed(0)}%</b><span>órdenes con retraso</span></div>
  </div>
  <div class="grid g2" style="margin-top:16px">
    <div class="card"><h2>Capacidad del taller</h2>${bar(bay,'Bahías')}${bar(slot,'Cajones de espera')}
      <p class="small">Tiempo promedio de reparación: ${k.tiempo_promedio_reparacion_horas ?? 'sin datos'} h</p>
      <div class="actions"><a href="#/recepcion"><button>Recibir un vehículo</button></a></div></div>
    <div class="card"><h2>Reglas que aplica el sistema</h2><ul style="margin:0;padding-left:18px;line-height:1.7">
      <li>Anticipo del <b>50%</b> si la cotización pasa de <b>$5,000</b></li>
      <li>Trabajo adicional desde <b>$800</b>: aprueba el cliente</li>
      <li>Más de <b>30%</b> de desviación: autoriza Gerencia</li>
      <li>Capacidad: <b>12 bahías y 8 cajones</b></li>
      <li>Cierre: reparada, pagada y firmada</li>
      <li>La bitácora no se edita ni se borra</li></ul></div>
  </div>
  <div class="card" style="margin-top:16px"><h2>Refacciones en riesgo de retraso</h2>
    ${table(['Orden','Refacción','Estado','Fecha límite'], risk.map(r=>({id:r.folio,cells:[`<a href="#/orden/${esc(r.folio)}">${esc(r.folio)}</a>`,esc(r.descripcion),badge(r.estado),fdate(r.fecha_limite)]})))}</div>`;
}

/* ---------- Recepción ---------- */
async function vRecepcion(){
  const a = await api('GET','/api/actores',{query:{limite:100,activo:'true'}});
  const opts = a.resultados.map(x=>`<option value="${esc(x.actor_id)}">${esc(x.actor_id)} · ${esc(x.nombre)} (${esc(x.ciudad)})${x.es_preferente?' ★':''}</option>`).join('');
  const fotos = [['FRENTE','Frente'],['ATRAS','Atrás'],['LADO_IZQUIERDO','Lado izquierdo'],['LADO_DERECHO','Lado derecho'],['DANOS','Daños visibles']];
  view().innerHTML = `
  <h1>Recepción de vehículo</h1><p class="sub">Abre una orden de servicio. Se exigen los datos del cliente y las 5 fotografías.</p>
  <form id="fRec" class="card">
    <div class="row"><div><label class="f" for="actor">Propietario (actor activo)</label><select id="actor">${opts}</select></div>
      <div><label class="f" for="cli">Nombre del cliente</label><input id="cli" required minlength="2"></div>
      <div><label class="f" for="tel">Teléfono</label><input id="tel" required minlength="7"></div></div>
    <div class="row"><div><label class="f" for="cor">Correo (opcional)</label><input id="cor" type="email"></div>
      <div><label class="f" for="pla">Placas</label><input id="pla" required minlength="4"></div>
      <div><label class="f" for="vin">VIN</label><input id="vin" required minlength="11"></div></div>
    <div class="row"><div><label class="f" for="ase">Aseguradora (opcional)</label><input id="ase"></div>
      <div><label class="f" for="pol">Póliza (opcional)</label><input id="pol"></div>
      <div><label class="f" for="flo">Flotilla con contrato</label><select id="flo"><option value="false">No</option><option value="true">Sí (puede entrar con el taller lleno)</option></select></div></div>
    <h2 style="margin-top:18px">Evidencia fotográfica</h2>
    <div class="row">${fotos.map(([k,l])=>`<div><label class="f" for="f_${k}">${l}</label><input id="f_${k}" required placeholder="https://…"></div>`).join('')}</div>
    <div class="actions"><button type="submit">Abrir orden</button><button type="button" class="ghost" id="demo">Rellenar con datos de ejemplo</button></div>
    <div id="out"></div>
  </form>`;
  $('#demo').onclick = () => {
    const r = Math.random().toString(36).slice(2,8).toUpperCase();
    $('#cli').value='Ana Pérez'; $('#tel').value='8112345678'; $('#pla').value='DEM'+r.slice(0,4); $('#vin').value='1HGCM8263'+r+'01';
    fotos.forEach(([k])=>$('#f_'+k).value='https://ejemplo.test/fotos/'+k.toLowerCase()+'.jpg');
  };
  $('#fRec').onsubmit = async ev => {
    ev.preventDefault();
    const btn = $('button[type=submit]', ev.target);
    const ev2 = {}; fotos.forEach(([k])=>ev2[k]=val('f_'+k));
    const body = {actor_id:val('actor'), nombre_cliente:val('cli'), telefono:val('tel'), placas:val('pla'), vin:val('vin'),
      flotilla_contrato: val('flo')==='true', evidencias: ev2};
    if(val('cor')) body.correo = val('cor'); if(val('ase')) body.aseguradora = val('ase'); if(val('pol')) body.poliza = val('pol');
    btn.disabled = true;
    try{
      const r = await api('POST','/api/ordenes',{body});
      $('#out').innerHTML = `<div class="msg good"><b>Orden abierta: ${esc(r.folio)}</b> · ${badge(r.estado)}<br>Bahía asignada: ${r.bahia_asignada?'sí':'no'} · Cajón asignado: ${r.cajon_asignado?'sí':'no'} · Apertura: ${fdate(r.fecha_apertura)}<br><a href="#/orden/${esc(r.folio)}"><button class="ok" type="button" style="margin-top:10px">Abrir la orden</button></a></div>`;
      toast('Orden '+r.folio+' creada');
    }catch(e){ $('#out').innerHTML = `<div class="msg warn"><b>No se pudo abrir la orden.</b><br>${esc(e.message)}</div>`; toast(e.message,true); }
    finally{ btn.disabled = false; }
  };
}

/* ---------- Órdenes ---------- */
async function vOrdenes(){
  view().innerHTML = `<h1>Órdenes de servicio</h1><p class="sub">Las más recientes según la bitácora. También puedes buscar por folio.</p>
    <div class="card"><div class="row"><div style="flex:3"><label class="f" for="bf">Buscar por folio</label><input id="bf" placeholder="AF-20261112-ABC123"></div>
    <div style="align-self:end"><button id="bgo">Abrir</button></div></div></div>
    <div class="card" style="margin-top:16px" id="lista"><p class="sub">Cargando…</p></div>`;
  $('#bgo').onclick = () => { const f = val('bf').trim(); if(f) location.hash = '#/orden/'+encodeURIComponent(f); };
  const aud = await api('GET','/api/auditoria');
  const folios = [...new Set(aud.map(x=>x.folio).filter(Boolean))].slice(0,30);
  const res = await Promise.allSettled(folios.map(f=>api('GET','/api/ordenes/'+encodeURIComponent(f))));
  const rows = res.filter(r=>r.status==='fulfilled').map(r=>r.value);
  $('#lista').innerHTML = '<h2>Recientes</h2>' + table(['Folio','Cliente','Placas','Estado','Apertura'],
    rows.map(o=>({o, cells:[esc(o.folio),esc(o.cliente),esc(o.placas),badge(o.estado),fshort(o.fecha_apertura)]})),
    r=>`class="link" data-folio="${esc(r.o.folio)}"`);
  $$('tr.link').forEach(tr => tr.onclick = () => location.hash = '#/orden/'+encodeURIComponent(tr.dataset.folio));
}

/* ---------- Detalle de orden ---------- */
const store = (k, v) => v===undefined ? JSON.parse(sessionStorage.getItem(k)||'[]') : sessionStorage.setItem(k, JSON.stringify(v));
function card(title, roles, inner, id){
  const dim = roles.length && !roles.includes(role());
  return `<div class="card ${dim?'dim':''}" ${id?`id="${id}"`:''}><h2>${title}${roles.length?`<span class="tag">${roles.join(' / ')}</span>`:''}</h2>${dim?'<p class="small">Normalmente lo hace otro rol; la API no lo bloquea.</p>':''}${inner}</div>`;
}
async function vOrden(folio){
  const f = encodeURIComponent(folio);
  let o;
  try{ o = await api('GET','/api/ordenes/'+f); }
  catch(e){ view().innerHTML = `<h1>Orden no encontrada</h1><div class="msg warn">${esc(e.message)}</div><p><a href="#/ordenes">Volver a las órdenes</a></p>`; return; }
  const [quotes, evid, aud, evs] = await Promise.all([
    api('GET',`/api/ordenes/${f}/cotizaciones`).catch(()=>[]), api('GET',`/api/ordenes/${f}/evidencias`).catch(()=>[]),
    api('GET',`/api/ordenes/${f}/auditoria`).catch(()=>[]), api('GET','/api/eventos').catch(()=>[])]);
  const idx = ESTADOS.indexOf(o.estado);
  const steps = ESTADOS.map((e,i)=>`<div class="step ${i<idx?'done':''} ${i===idx?'now':''}">${esc(e.replace(/_/g,' '))}</div>`).join('');
  const sig = SIGUIENTE[o.estado] || ['',''];
  const nexts = TRANSICIONES[o.estado] || [];
  const adics = store('adic:'+folio), parts = store('part:'+folio);

  const qHtml = quotes.length ? quotes.map(q=>`<div class="mini"><b>${esc(q.cotizacion_id||q.id)}</b> · ${badge(q.estado)}<br>Total ${money(q.total_mxn ?? q.total)} · Anticipo requerido ${money(q.anticipo_requerido)}
    ${q.estado==='PENDIENTE'?`<div class="row" style="margin-top:8px"><input class="qfirma" placeholder="Firma del cliente" value=""><button class="ok qdec" data-id="${esc(q.cotizacion_id||q.id)}" data-ap="true">Aprobar</button><button class="bad qdec" data-id="${esc(q.cotizacion_id||q.id)}" data-ap="false">Rechazar</button></div>`:''}</div>`).join('') : '<div class="empty">Aún no hay cotizaciones.</div>';
  const aHtml = adics.length ? adics.map((a,i)=>`<div class="mini"><b>#${a.id}</b> ${esc(a.descripcion)} · ${money(a.monto)} · ${badge(a.estado)}<br><span class="small">Requiere cliente: ${a.req_cli?'sí':'no'} · Requiere Gerencia: ${a.req_ger?'sí':'no'}</span>
    <div class="row" style="margin-top:8px"><button class="ok adec" data-i="${i}" data-ap="true">Cliente aprueba</button><button class="bad adec" data-i="${i}" data-ap="false">Cliente rechaza</button>
    ${a.req_ger?`<button class="sec ager" data-i="${i}" data-ap="true">Gerencia autoriza</button><button class="ghost ager" data-i="${i}" data-ap="false">Gerencia rechaza</button>`:''}</div></div>`).join('') : '<div class="empty">Sin trabajos adicionales en esta sesión.</div>';
  const pHtml = parts.length ? parts.map((p,i)=>`<div class="mini"><b>#${p.id}</b> ${esc(p.descripcion)} · ${esc(p.proveedor)} · ${badge(p.estado)}<br><span class="small">Fecha límite: ${fdate(p.fecha_limite)}</span>
    <div class="row" style="margin-top:8px"><select class="pest"><option>RECIBIDA</option><option>AGOTADA</option><option>SOLICITADA</option></select><button class="ghost pupd" data-i="${i}">Cambiar estado</button></div></div>`).join('') : '<div class="empty">Sin refacciones en esta sesión.</div>';

  view().innerHTML = `
  <p class="small"><a href="#/ordenes">← Órdenes</a></p>
  <div class="card"><div class="row" style="align-items:center"><div><h1 style="margin:0">${esc(o.folio)}</h1><div class="sub" style="margin:4px 0 0">Apertura: ${fdate(o.fecha_apertura)}</div></div><div style="flex:0">${badge(o.estado)}</div></div>
    ${kvs([['Cliente',esc(o.cliente)],['Teléfono',esc(o.telefono)],['Placas',esc(o.placas)],['VIN',esc(o.vin)],['Técnico',esc(o.tecnico||'sin asignar')],['Total acumulado',money(o.total)]])}
    <div class="steps">${steps}</div></div>
  <div class="next"><b>Siguiente paso:</b> ${esc(sig[1])} ${sig[0]?`<span class="tag">${sig[0]}</span>`:''}</div>
  <div class="grid g2">
    ${card('1. Diagnóstico',['TECNICO'],`<label class="f" for="dg">Diagnóstico</label><textarea id="dg" placeholder="Ej.: Balatas gastadas"></textarea><label class="f" for="dt">Técnico</label><input id="dt" value="${esc(role()==='TECNICO'?user():'tecnico1')}"><div class="actions"><button id="bDiag">Registrar diagnóstico</button><button class="ghost" id="bAcep">El técnico acepta la unidad</button></div>`)}
    ${card('2. Cotización',['ASESOR'],`<div id="lineas"></div><div class="row"><select id="cat"><option value="">Agregar del catálogo…</option></select><button class="ghost" id="addL" type="button">+ Línea manual</button></div><div class="actions"><button id="bCot">Crear cotización</button></div>${qHtml}`)}
    ${card('3. Pagos y anticipo',['CAJERO'],`<div class="row"><div><label class="f" for="pm">Monto</label><input id="pm" type="number" min="0.01" step="0.01"></div><div><label class="f" for="pt">Tipo</label><select id="pt"><option>ANTICIPO</option><option>PAGO</option></select></div></div><div class="actions"><button id="bPago">Registrar pago</button></div><div id="pout"></div>`)}
    ${card('4. Cambiar el estado',['ASESOR','TECNICO'],nexts.length?`<label class="f" for="dest">Nuevo estado</label><select id="dest">${nexts.map(n=>`<option>${n}</option>`).join('')}</select><div class="actions"><button id="bTr">Cambiar estado</button></div><p class="small">Si falta el anticipo o la transición no es válida, el sistema la rechaza y lo registra en la bitácora.</p>`:'<div class="empty">Esta orden ya no admite cambios de estado.</div>')}
    ${card('5. Trabajos adicionales',['ASESOR','GERENTE'],`<div class="row"><div style="flex:2"><label class="f" for="ad">Descripción</label><input id="ad"></div><div><label class="f" for="am">Monto</label><input id="am" type="number" min="0.01" step="0.01"></div></div><div class="actions"><button id="bAd">Registrar trabajo adicional</button></div><p class="small">Desde $800 requiere al cliente; si el acumulado pasa de 30%, también a Gerencia.</p>${aHtml}`)}
    ${card('6. Refacciones',['TECNICO'],`<label class="f" for="rd">Refacción</label><input id="rd"><label class="f" for="rp">Proveedor</label><div class="row"><div><select id="rp"><option value="LOCAL">Local (24 h)</option><option value="NACIONAL">Nacional (72 h)</option><option value="IMPORTACION">Importación (7 días)</option></select></div></div><div class="actions"><button id="bPart">Solicitar refacción</button></div>${pHtml}`)}
    ${card('7. Retraso en la entrega',['ASESOR'],`<div class="row"><div><label class="f" for="rh">Horas de retraso</label><input id="rh" type="number" min="1" value="5"></div><div><label class="f" for="rf">Nueva fecha estimada</label><input id="rf" type="datetime-local"></div></div><div class="actions"><button id="bRet">Registrar retraso</button></div><p class="small">Un retraso mayor a 4 horas exige avisar al cliente el mismo día.</p><div id="rout"></div>`)}
    ${card('8. Entrega, almacenaje y cierre',['CAJERO','ASESOR'],`<div class="row"><div><label class="f" for="ll">Llamadas de recolección (0 a 3)</label><input id="ll" type="number" min="0" max="3" value="0"></div></div><div class="actions"><button class="ghost" id="bLl">Registrar llamadas</button><button class="ghost" id="bAlm">Calcular almacenaje</button></div><div id="aout"></div><hr style="border:0;border-top:1px solid var(--line);margin:16px 0"><label class="f" for="fc">Firma de conformidad del cliente</label><input id="fc" placeholder="Nombre del cliente"><div class="actions"><button class="ok" id="bCerrar">Cerrar orden</button></div><p class="small">Se cierra solo si está reparada, con pago total y firmada.</p>`)}
    ${card('Evidencias fotográficas',[],`${table(['Tipo','Etapa','Enlace'],evid.map(e=>({cells:[esc(e.tipo),esc(e.etapa||''),`<a href="${esc(e.url)}" target="_blank" rel="noopener">ver</a>`]})))}`)}
  </div>
  <div class="card" style="margin-top:16px"><h2>Bitácora de la orden</h2>${table(['Fecha','Usuario','Rol','Acción','Detalle'],aud.slice().reverse().map(a=>({cells:[fshort(a.fecha),esc(a.usuario),esc(a.rol),esc(a.accion),`<span class="small">${esc(JSON.stringify(a.detalle||{}))}</span>`]})))}
  <p class="small" style="margin-top:12px">Eventos de dominio de esta orden: ${evs.filter(e=>e.folio===folio).map(e=>esc(e.nombre)).reverse().join(', ')||'ninguno'}.</p></div>`;

  const refresh = () => vOrden(folio);
  /* líneas de cotización */
  const lineas = $('#lineas');
  const addLine = (d='',c='REFACCION',q=1,p='') => { const el = document.createElement('div'); el.className='line';
    el.innerHTML = `<input placeholder="Descripción" value="${esc(d)}" class="ld"><select class="lc"><option>REFACCION</option><option>MANO_DE_OBRA</option><option>SERVICIO</option></select><input class="lq" type="number" min="1" value="${q}" title="Cantidad" placeholder="Cant."><input class="lp" type="number" min="0" step="0.01" placeholder="Precio unitario" value="${p}"><button type="button" class="ghost lx">✕</button>`;
    el.querySelector('.lc').value = c; el.querySelector('.lx').onclick = () => el.remove(); lineas.appendChild(el); };
  addLine();
  $('#addL').onclick = () => addLine();
  api('GET','/api/catalogo').then(cat => { const sel = $('#cat'); cat.filter(x=>x.habilitado).forEach(x=>{ const op=document.createElement('option'); op.value=JSON.stringify([x.descripcion,x.precio_mxn]); op.textContent=`${x.descripcion} · ${money(x.precio_mxn)}`; sel.appendChild(op); });
    sel.onchange = () => { if(sel.value){ const [d,p]=JSON.parse(sel.value); addLine(d,'SERVICIO',1,p); sel.value=''; } }; }).catch(()=>{});

  $('#bDiag').onclick = e => act(e.target, async()=>{ await api('POST',`/api/ordenes/${f}/diagnostico`,{query:{diagnostico:val('dg'),tecnico:val('dt')}}); await refresh(); }, 'Diagnóstico registrado');
  $('#bAcep').onclick = e => act(e.target, async()=>{ await api('POST',`/api/ordenes/${f}/aceptacion-tecnico`,{query:{tecnico:val('dt')}}); await refresh(); }, 'Aceptación del técnico registrada');
  $('#bCot').onclick = e => act(e.target, async()=>{
    const ls = $$('.line',lineas).map(l=>({descripcion:l.querySelector('.ld').value,categoria:l.querySelector('.lc').value,cantidad:Number(l.querySelector('.lq').value),precio_unitario:l.querySelector('.lp').value}));
    const r = await api('POST',`/api/ordenes/${f}/cotizaciones`,{body:{lineas:ls}}); await refresh(); return r; }, r=>`Cotización ${r.cotizacion_id}: ${money(r.total_mxn)}, anticipo ${money(r.anticipo_requerido)}`);
  $$('.qdec').forEach(b => b.onclick = e => { const firma = e.target.closest('.mini').querySelector('.qfirma').value;
    act(e.target, async()=>{ await api('POST',`/api/cotizaciones/${b.dataset.id}/decision`,{body:{aprobado:b.dataset.ap==='true',firma,usuario:ascii(user())}}); await refresh(); }, 'Decisión del cliente registrada'); });
  $('#bPago').onclick = e => act(e.target, async()=>{ const r = await api('POST',`/api/ordenes/${f}/pagos`,{body:{monto:val('pm'),tipo:val('pt')}});
    $('#pout').innerHTML = `<div class="msg good">Pagado hasta ahora: ${money(r.monto_pagado)} · Pago total: ${r.pago_total?'sí':'no'}</div>`; return r; }, 'Pago registrado');
  if($('#bTr')) $('#bTr').onclick = e => act(e.target, async()=>{ await api('POST',`/api/ordenes/${f}/transiciones`,{body:{estado_destino:val('dest'),usuario:ascii(user()),rol:role()}}); await refresh(); }, 'Estado actualizado');
  $('#bAd').onclick = e => act(e.target, async()=>{ const d = val('ad'), m = val('am');
    const r = await api('POST',`/api/ordenes/${f}/trabajos-adicionales`,{body:{descripcion:d,monto:m,asesor:ascii(user())}});
    const l = store('adic:'+folio); l.push({id:r.id,descripcion:d,monto:m,estado:r.estado,req_cli:r.requiere_aprobacion_cliente,req_ger:r.requiere_autorizacion_gerencia}); store('adic:'+folio,l); await refresh(); }, 'Trabajo adicional registrado');
  const updAd = (i, estado) => { const l = store('adic:'+folio); l[i].estado = estado; store('adic:'+folio,l); };
  $$('.adec').forEach(b => b.onclick = e => act(e.target, async()=>{ const l = store('adic:'+folio)[b.dataset.i];
    const r = await api('POST',`/api/trabajos-adicionales/${l.id}/decision`,{body:{aprobado:b.dataset.ap==='true',usuario:ascii(user())}}); updAd(b.dataset.i,r.estado); await refresh(); }, 'Decisión del cliente registrada'));
  $$('.ager').forEach(b => b.onclick = e => act(e.target, async()=>{ const l = store('adic:'+folio)[b.dataset.i];
    const r = await api('POST',`/api/trabajos-adicionales/${l.id}/autorizacion-gerencia`,{query:{aprobado:b.dataset.ap,gerente:ascii(user())}}); updAd(b.dataset.i,r.estado||'AUTORIZADO'); await refresh(); }, 'Decisión de Gerencia registrada'));
  $('#bPart').onclick = e => act(e.target, async()=>{ const d=val('rd'), p=val('rp');
    const r = await api('POST',`/api/ordenes/${f}/refacciones`,{body:{descripcion:d,proveedor_tipo:p}});
    const l = store('part:'+folio); l.push({id:r.id,descripcion:d,proveedor:p,estado:r.estado,fecha_limite:r.fecha_limite}); store('part:'+folio,l); await refresh(); }, 'Refacción solicitada');
  $$('.pupd').forEach(b => b.onclick = e => act(e.target, async()=>{ const l = store('part:'+folio), p = l[b.dataset.i], est = b.closest('.mini').querySelector('.pest').value;
    await api('PATCH','/api/refacciones/'+p.id,{body:{estado:est}}); p.estado = est; store('part:'+folio,l); await refresh(); }, 'Estado de la refacción actualizado'));
  $('#bRet').onclick = e => act(e.target, async()=>{ const fe = val('rf'); if(!fe) throw new Error('Elige la nueva fecha estimada de entrega');
    const r = await api('POST',`/api/ordenes/${f}/retraso`,{body:{horas_retraso:Number(val('rh')),fecha_estimada_entrega:fe,usuario:ascii(user())}});
    $('#rout').innerHTML = `<div class="msg ${r.notificacion_requerida?'warn':'good'}">${r.notificacion_requerida?'<b>Hay que avisar al cliente hoy</b> (retraso mayor a 4 horas).':'Retraso registrado; no requiere aviso inmediato.'}</div>`; return r; }, 'Retraso registrado');
  $('#bLl').onclick = e => act(e.target, async()=>{ const r = await api('POST',`/api/ordenes/${f}/llamadas-recoleccion`,{body:{llamadas_recoleccion:Number(val('ll'))}});
    $('#aout').innerHTML = `<div class="msg good">${esc(JSON.stringify(r))}</div>`; return r; }, 'Llamadas registradas');
  $('#bAlm').onclick = e => act(e.target, async()=>{ const r = await api('POST',`/api/ordenes/${f}/calcular-almacenaje`);
    $('#aout').innerHTML = `<div class="msg good">Días cobrables: <b>${r.dias_cobrables}</b> · Cargo de almacenaje: <b>${money(r.cargo_mxn)}</b> ($100 por día tras 15 días y 3 llamadas)</div>`; return r; });
  $('#bCerrar').onclick = e => act(e.target, async()=>{ await api('POST',`/api/ordenes/${f}/cerrar`,{body:{firma_conformidad:val('fc'),usuario:ascii(user())}}); await refresh(); }, 'Orden cerrada');
}

/* ---------- Consultas ---------- */
async function vConsultas(){
  view().innerHTML = `<h1>Consultas</h1><p class="sub">Lo que ve el cliente y el historial de cada vehículo.</p>
  <div class="grid g2">
    <div class="card"><h2>Portal del cliente</h2><label class="f" for="pf">Folio</label><input id="pf" placeholder="AF-…"><div class="actions"><button id="bPor">Consultar estatus</button></div><div id="porOut"></div></div>
    <div class="card"><h2>Historial del vehículo</h2><label class="f" for="hv">VIN</label><input id="hv"><div class="actions"><button id="bHis">Ver historial</button></div><div id="hisOut"></div></div>
    <div class="card"><h2>Indicadores de otro día</h2><label class="f" for="kd">Fecha</label><input id="kd" type="date"><div class="actions"><button id="bK">Ver indicadores</button></div><div id="kOut"></div></div>
  </div>`;
  const fail = (id,e) => { $(id).innerHTML = `<div class="msg warn">${esc(e.message)}</div>`; };
  $('#bPor').onclick = async e => { try{ const r = await api('GET','/api/portal/'+encodeURIComponent(val('pf').trim()));
    $('#porOut').innerHTML = `<div class="msg good">${kvs([['Folio',esc(r.folio)],['Placas',esc(r.placas)],['Estatus',badge(r.estado)],['Apertura',fdate(r.fecha_apertura)],['Entrega estimada',fdate(r.fecha_estimada_entrega)]])}</div>`; }catch(er){ fail('#porOut',er); } };
  $('#bHis').onclick = async e => { try{ const r = await api('GET',`/api/vehiculos/${encodeURIComponent(val('hv').trim())}/historial`);
    $('#hisOut').innerHTML = r.length ? r.map(h=>`<div class="mini"><b>${esc(h.folio)}</b> · ${badge(h.estado)} · ${fdate(h.fecha_apertura)}<br>${h.lineas.map(l=>`${esc(l.descripcion)} ×${l.cantidad}`).join(', ')||'sin líneas'}<br><span class="small">Garantías: ${h.garantias.length} · Recomendaciones pendientes: ${h.recomendaciones_pendientes.length}</span></div>`).join('') : '<div class="empty">Ese VIN no tiene servicios registrados.</div>'; }catch(er){ fail('#hisOut',er); } };
  $('#bK').onclick = async e => { try{ const r = await api('GET','/api/kpis/diarios',{query:{fecha:val('kd')}});
    $('#kOut').innerHTML = `<div class="msg good">${kvs([['Fecha',esc(r.fecha)],['Abiertas',r.ordenes_abiertas],['Cerradas',r.ordenes_cerradas],['Ingresos',money(r.ingresos_mxn)],['Bahías',esc(r.ocupacion_bahias)],['Cajones',esc(r.ocupacion_cajones)]])}</div>`; }catch(er){ fail('#kOut',er); } };
}

/* ---------- Auditoría ---------- */
async function vAuditoria(){
  const [aud, evs] = await Promise.all([api('GET','/api/auditoria'), api('GET','/api/eventos')]);
  view().innerHTML = `<h1>Auditoría y eventos</h1><p class="sub">La bitácora es de solo inserción: no se puede editar ni borrar, ni siquiera por Gerencia.</p>
  <div class="card"><h2>Probar la inalterabilidad</h2><p class="small">Intenta borrar la primera entrada de la bitácora.</p><div class="actions"><button class="bad" id="bDel">Intentar borrar una entrada</button></div><div id="delOut"></div></div>
  <div class="card" style="margin-top:16px"><h2>Bitácora (últimas 100)</h2>${table(['Fecha','Folio','Usuario','Rol','Acción'],aud.slice(0,100).map(a=>({cells:[fshort(a.fecha),`<a href="#/orden/${esc(a.folio)}">${esc(a.folio||'')}</a>`,esc(a.usuario),esc(a.rol),esc(a.accion)]})))}</div>
  <div class="card" style="margin-top:16px"><h2>Eventos de dominio (últimos 100)</h2>${table(['Fecha','Folio','Evento'],evs.slice(0,100).map(x=>({cells:[fshort(x.occurred_at),`<a href="#/orden/${esc(x.folio)}">${esc(x.folio||'')}</a>`,esc(x.nombre)]})))}</div>`;
  $('#bDel').onclick = async () => { const id = aud.length ? aud[aud.length-1].id : 1;
    const res = await fetch('/api/auditoria/'+id,{method:'DELETE'}); let d = null; try{ d = await res.json(); }catch(_){}
    $('#delOut').innerHTML = `<div class="msg ${res.status===405?'good':'warn'}">Respuesta del servidor: <b>HTTP ${res.status}</b>. ${res.status===405?'La bitácora rechazó el borrado.':esc(JSON.stringify(d))}</div>`; };
}

/* ---------- Router ---------- */
async function route(){
  const h = location.hash.replace(/^#\/?/, '') || 'inicio';
  const [name, ...rest] = h.split('/');
  $$('#nav a').forEach(a => a.classList.toggle('on', a.dataset.r === (name==='orden'?'ordenes':name)));
  view().innerHTML = '<p class="sub">Cargando…</p>';
  try{
    if(name==='inicio') await vInicio();
    else if(name==='recepcion') await vRecepcion();
    else if(name==='ordenes') await vOrdenes();
    else if(name==='orden') await vOrden(decodeURIComponent(rest.join('/')));
    else if(name==='consultas') await vConsultas();
    else if(name==='auditoria') await vAuditoria();
    else await vInicio();
  }catch(e){ view().innerHTML = `<div class="msg warn"><b>No se pudo cargar la pantalla.</b><br>${esc(e.message)}</div>`; }
  window.scrollTo(0,0);
}
function initWho(){
  const sel = $('#role'); sel.innerHTML = Object.keys(ROLES).map(r=>`<option>${r}</option>`).join(''); sel.value = role();
  $('#user').value = user();
  sel.onchange = () => { localStorage.setItem('af_role', sel.value); localStorage.setItem('af_user', ROLES[sel.value]); $('#user').value = ROLES[sel.value]; route(); };
  $('#user').onchange = () => localStorage.setItem('af_user', $('#user').value || ROLES[role()]);
}
window.addEventListener('hashchange', route);
initWho(); route();
</script>
</body>
</html>
"""
