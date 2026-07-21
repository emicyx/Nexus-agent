"""OutputSchema 配置 CRUD 路由"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.schemas.output_schema import OutputSchemaCreate, OutputSchemaRead, OutputSchemaUpdate
from app.services import output_schema_service

router = APIRouter()


@router.get("", response_model=list[OutputSchemaRead])
async def list_schemas(session: AsyncSession = Depends(get_db)):
    return await output_schema_service.list_schemas(session)


@router.get("/{schema_id}", response_model=OutputSchemaRead)
async def get_schema(schema_id: int, session: AsyncSession = Depends(get_db)):
    schema_ = await output_schema_service.get_schema(session, schema_id)
    if schema_ is None:
        raise HTTPException(404, "OutputSchema not found")
    return schema_


@router.post("", response_model=OutputSchemaRead, status_code=201)
async def create_schema(payload: OutputSchemaCreate, session: AsyncSession = Depends(get_db)):
    return await output_schema_service.create_schema(session, payload)


@router.put("/{schema_id}", response_model=OutputSchemaRead)
async def update_schema(schema_id: int, payload: OutputSchemaUpdate, session: AsyncSession = Depends(get_db)):
    schema_ = await output_schema_service.update_schema(session, schema_id, payload)
    if schema_ is None:
        raise HTTPException(404, "OutputSchema not found")
    return schema_


@router.delete("/{schema_id}", status_code=204)
async def delete_schema(schema_id: int, session: AsyncSession = Depends(get_db)):
    ok = await output_schema_service.delete_schema(session, schema_id)
    if not ok:
        raise HTTPException(404, "OutputSchema not found")
