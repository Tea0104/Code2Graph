import python

// 查询模块之间的导入关系，输出源模块和被导入模块。

from Module m, Module imported
where imported.getName() = m.getAnImportedModuleName()
select
  "IMPORTS" as rel,
  m.getLocation().getFile().getRelativePath() as fromFile,
  m.getName() as fromName,
  1 as fromLine,
  imported.getLocation().getFile().getRelativePath() as toFile,
  imported.getName() as toName,
  1 as toLine
