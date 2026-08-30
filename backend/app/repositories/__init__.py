"""数据库访问边界。

Repository 只负责 SQL、ORM 持久化和数据库锁操作；事务由 Service 持有。
业务函数不在此处批量 re-export，调用方应从具体 Repository 模块导入。
"""
