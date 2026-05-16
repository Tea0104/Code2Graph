import csv
from pathlib import Path
from dataclasses import dataclass,asdict

#读单个csv文件
def read_csv(path:Path):
    #判断是csv还是tsv（分隔符是 , 还是 \t）
    delimiter = "\t" if path.suffix.lower() == ".tsv" else ","

    with path.open(encoding="utf-8-sig",newline="")as handle:
        #创建字典，第一行是表头，后续每行映射成字典
        reader=csv.DictReader(handle,delimiter=delimiter)

        #保存数据
        all_rows=[]
        for row in reader:
            all_rows.append(row)
        
        return all_rows
    

result_files={
    "nodes_modules": [],
    "nodes_classes": [],
    "nodes_callables": [],
    "nodes_imports": [],
    "nodes_parameters": [],
    "nodes_classAttribute": [],
    #"nodes_variables": [],
    "edges_calls":[],
    "edges_defines":[],
    "edges_imports":[],
    "edges_inherits":[],
    "edges_references":[],
}

#对result_files的引用
NODES = {
    "nodes_modules": result_files["nodes_modules"],
    "nodes_classes": result_files["nodes_classes"],
    "nodes_callables": result_files["nodes_callables"],
    "nodes_imports": result_files["nodes_imports"],
    "nodes_parameters": result_files["nodes_parameters"],
    "nodes_classAttribute": result_files["nodes_classAttribute"],
    #"nodes_variables": result_files["nodes_variables"],
}

EDGES = {
    "edges_calls":result_files["edges_calls"],
    "edges_defines":result_files["edges_defines"],
    "edges_imports":result_files["edges_imports"],
    "edges_inherits":result_files["edges_inherits"],
    "edges_references":result_files["edges_references"],
}

#按照查询文件名称分别保存查询文件的结果
def load_csv(path):
    p=Path(path)
    prefix=p.stem
    all_rows=read_csv(path)
    result_files[prefix].extend(all_rows)


#存入查询内容到result_files之后
#1.创建所有节点
nodes={} #nodes={"node_id":{row},...}

@dataclass
class Node:
    id:str
    kind:str 
    name:str
    file:str
    startline:int
    endline:int

    def to_dict(self):
        return asdict(self)
    

def create_nodes():
    for key in NODES.values():
        for value in key:
            kind=value["kind"]
            name=value["name"]
            file=value["file"]
            startline=value["startLine"] 
            endline=value["endLine"]
            node_id=f"{file}:{name}:{startline}"
            node=Node(node_id,kind,name,file,startline,endline).to_dict()
            nodes[node_id]=node

#2.加入边
edges={}

@dataclass
class Edge:  #保存了起点、终点，用于建图
    edge_id:str
    source:str
    target:str
    kind:str

    def to_dict(self):
        return asdict(self)


def ensure_node(node_id: str, file: str, name: str, line: str | int | None):
    if node_id in nodes:
        return nodes[node_id]
    node = Node(node_id, "Entity", name, file, line or 0, None).to_dict()
    nodes[node_id] = node
    return node


def create_edges():
    for key in EDGES.values():
        for value in key:
            rel=value["rel"]
            fromFile=value["fromFile"]
            fromName=value["fromName"]
            fromLine=value["fromLine"]
            toFile=value["toFile"]
            toName=value["toName"]
            toLine=value["toLine"]
            source_id=f"{fromFile}:{fromName}:{fromLine}"
            target_id=f"{toFile}:{toName}:{toLine}"
            edge_id=f"{source_id}:{target_id}:{rel}"
            source=ensure_node(source_id, fromFile, fromName, fromLine)
            target=ensure_node(target_id, toFile, toName, toLine)
            edge=Edge(edge_id,source,target,rel).to_dict()
            edges[edge_id]=edge



