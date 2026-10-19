# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.
from pydantic.v1 import BaseModel


class BaseNode(BaseModel):
    '''This class represents the base node in the graph database.'''
    id: str
    attributes: dict = None
