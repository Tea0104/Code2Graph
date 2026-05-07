import python

// 查询导入关系，输出 import 语句和它引用的模块名。

from Import i
select
  "IMPORTS" as rel,
  i.getLocation().getFile().getRelativePath() as fromFile,
  i.toString() as fromName,
  i.getLocation().getStartLine() as fromLine,
  i.getAnImportedModuleName() as toFile,
  i.getAnImportedModuleName() as toName,
  1 as toLine
