# 2.7 官方来源核对摘录

读取日期2026-09-22。仅获取公开文档/元数据；未下载权重、调用推理或发送业务文本。完整请求URL、时间、响应字节数与SHA见 [sources.json](sources.json)。缓存原文在忽略目录work中，交付保留以下必要摘录；公网内容可能变化，模型已固定revision。

## E5-small

来源：[固定revision模型卡](https://huggingface.co/intfloat/multilingual-e5-small/blob/614241f622f53c4eeff9890bdc4f31cfecc418b3/README.md)、[官方元数据](https://huggingface.co/api/models/intfloat/multilingual-e5-small?blobs=true)。元数据许可证mit，参数计数117,654,272（包含512个I64条目）；FP32权重470,641,600字节。文件SHA仅来自服务端LFS元数据，实物下载核验留到2.8。

> This model has 12 layers and the embedding size is 384.
> # Each input text should start with "query: " or "passage: ", even for non-English texts.
> It supports 100 languages from xlm-roberta,
> Long texts will be truncated to at most 512 tokens.

固定revision的1_Pooling/config.json确认mean pooling，sentence_bert_config.json确认max_seq_length=512；已读取并记录到sources.json。模型卡给出的长文本截断行为不适合默默截去政策，本项目计划显式检查超限。

## BGE-M3

来源：[官方模型卡](https://huggingface.co/BAAI/bge-m3)、[仓库元数据](https://huggingface.co/api/models/BAAI/bge-m3?blobs=true)。模型卡表格为1024维、8192 tokens、多语言；许可证mit。读取到的revision为5617a9f61b028005a4858fdac845db406aefb181，pytorch_model.bin为2,271,145,830字节；未下载或加载该文件，不作为实测内存占用。

## 云端价格

来源：[百炼text-embedding-v4](https://help.aliyun.com/zh/model-studio/text-embedding-v4)、[模型价格总表](https://help.aliyun.com/zh/model-studio/model-pricing)。两页交叉核对北京标准0.5元/百万输入tokens、新加坡国际0.514元/百万输入tokens；北京Batch File为0.25元。本项目预算按标准调用计算，不假设免费额度。下面是模型详情页价格区域的文本摘录：

```text
模型价格
本文仅展示模型调用原价，不包含限时优惠等活动信息，请前往
百炼控制台
查看活动优惠。
华北
2（北京）
计费项
价格（元）
单位
向量输入（Batch File）
0.25
每百万
tokens
文本输入
0.5
每百万
tokens
新加坡
部署范围：国际
计费项
价格（元）
单位
文本输入
0.514
每百万
tokens
```

不将厂商通用榜单或相对性能宣传作为ResolveFlow上的效果证据；端点、账号、地域权限、实际分词和账单未测。

## pgvector

来源：[官方README](https://github.com/pgvector/pgvector)。当前读取的安装版本为0.8.6，列出PostgreSQL 17镜像候选。以下“perfect recall”仅指精确向量最近邻，不表示政策证据召回100%。

> Compile and install the extension (supports Postgres 13+)
> By default, pgvector performs exact nearest neighbor search, which provides perfect recall.
> Each vector takes `4 * dimensions + 8` bytes of storage. Each element is a single-precision floating-point number (like the `real` type in Postgres), and all elements must be finite (no `NaN`, `Infinity` or `-Infinity`). Vectors can have up to 16,000 dimensions.
> - `pg17-trixie`, `0.8.6-pg17-trixie`
