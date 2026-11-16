"""用几何端点验证选择，不引入 COMSOL 或校准求解。"""
import ast
from pathlib import Path

import pytest

from app.comsol.boundary_selection import rectangle_boundary_map


class Component:
    def __init__(self, width, height):
        self.edges={17:((0,0),(0,height)), 5:((width,0),(width,height)),
                    21:((0,0),(width,0)), 9:((0,height),(width,height))}
        self.items=[]

    def selection(self):
        return self

    def create(self, tag, kind):
        assert kind=='Box'
        selection=Selection(self.edges)
        self.items.append(selection)
        return selection


class Selection:
    def __init__(self, edges):
        self.edges,self.properties=edges,{}

    def geom(self, tag, dimension):
        assert (tag,dimension)==('geom1',1)

    def set(self, name, value):
        self.properties[name]=value

    def entities(self):
        p=self.properties
        assert p['condition']=='inside'
        return [edge for edge,vertices in self.edges.items() if all(
            p['xmin']<=x<=p['xmax'] and p['ymin']<=y<=p['ymax'] for x,y in vertices)]


@pytest.mark.parametrize(('width','height'),[(2,1),(10,5),(.01,.005),(1,1)])
def test_selection_does_not_assume_edge_order_or_geometry_unit(width,height):
    component=Component(width,height)
    assert rectangle_boundary_map(component,width,height)=={'left':17,'right':5,'bottom':21,'top':9}


@pytest.mark.parametrize(('width','height'),[(0,1),(-1,2),(float('nan'),1),(1,float('inf'))])
def test_invalid_extents_are_rejected_without_calls(width,height):
    component=Component(width,height)
    with pytest.raises(ValueError):
        rectangle_boundary_map(component,width,height)
    assert component.items==[]


def test_missing_or_ambiguous_edge_is_rejected():
    component=Component(2,1)
    component.edges[18]=component.edges[17]
    with pytest.raises(ValueError,match='one coordinate-selected left'):
        rectangle_boundary_map(component,2,1)


def test_worker_has_one_target_study_run_and_no_calibration_run():
    worker=Path(__file__).parents[1]/'app/comsol/heat_worker.py'
    tree=ast.parse(worker.read_text(encoding='utf-8'))
    calls=[node for node in ast.walk(tree) if isinstance(node,ast.Call) and isinstance(node.func,ast.Attribute)
           and isinstance(node.func.value,ast.Name) and node.func.value.id=='study' and node.func.attr=='run']
    assert len(calls)==1
