# Rtzip
_一个以最小的数据量提取远程 zip 文件内容的库_


## 特点
- 不与任何 HTTP 库耦合
- 依赖简单（只有用于解析二进制结构的 `obstruct`）
- 兼容标准 zip 格式、ZIP64 扩展


## 安装
```commandline
pip install rtzip
```

> [!TIP]
> 本库无计划新功能，若是让自行添加功能，可克隆仓库并重写 `rtzip/remotezip.py` 下的代码
> 大多数库的功能已完成并可以直接投入使用