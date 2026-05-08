/**
 * @name Reference Edges
 * @description Extracts variable reference relationships.
 * @kind table
 * @id python/references
 */

import python

// 查询变量引用关系，连接作用域与其被引用的变量。

from Name n, Variable v, Scope scope
where n.getScope() = scope and n.uses(v)
select
  "REFERENCES" as rel,
  scope.getLocation().getFile().getRelativePath() as fromFile,
  scope.getName() as fromName,
  scope.getLocation().getStartLine() as fromLine,
  v.getAnAccess().getLocation().getFile().getRelativePath() as toFile,
  v.getId() as toName,
  v.getAnAccess().getLocation().getStartLine() as toLine
