import python

// 查询 Python 模块节点，输出模块名、文件和位置。
//.py文件对应一个Module，包目录、__init__.py也会被当作Module

from Module m
select
  "Module" as kind,
  m.getLocation().getFile().getRelativePath() as file,
  m.getLocation().getFile().getBaseName() as name,
  1 as startLine,
  1 as endLine
