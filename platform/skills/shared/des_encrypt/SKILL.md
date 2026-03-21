# DES 安全加密接入 Skill

为 Spring Boot + MyBatis 项目接入国安 DES (Data Encryption Service) 加密服务，对数据库中的敏感字段（手机号、邮箱等）进行 SM4/GCM 加密存储。

---

## 使用方式

```
/des-encrypt
```

执行后 Claude 会引导你完成以下流程。你只需回答几个问题即可。

---

## 前置条件

- Spring Boot 项目（2.x 或 3.x），使用 MyBatis XML Mapper
- 已从 DES 团队获取：quickapi-client-java JAR 包、密钥 ID、加密服务 IP、证书文件
- 已明确需要加密的表和字段

## GitHub Issue 最小改造模式

当该技能由 GitHub Issue 工作流触发，且 issue 只给出明确字段范围（例如“对 `phone` 字段进行安全加密”），但没有提供完整的 DES 基础设施信息时，优先采用**最小改造模式**：

1. 只围绕 issue 指定的字段落地最小可交付闭环。
2. 优先修改与该字段直接相关的实体类、Mapper XML、必要的加密支撑类与最小验证代码。
3. 不要默认扩展到无直接关系的文件，例如日志配置、代码生成器、环境模板、整仓库批量字段改造。
4. 若缺少 `keyId`、JAR、服务 IP、证书等外部依赖信息，不要停在反问阶段；可以先按仓库现状完成代码骨架和接入点，并把外部依赖列入结果中的待办事项。
5. 若 issue 没有要求整套 DES 上线，就不要自动生成超出字段范围的大规模基础设施改造。

---

## 执行流程

### Step 1：收集信息

请用户提供以下信息（若未提供则逐一询问）：

1. **需要加密的表和字段**，格式如：
   - `表名: 字段1, 字段2`
   - 例：`alipay_refund: phone`、`alipay_receipt: phone, email`
2. **密钥 ID**（DES 平台申请的 keyId，如 `o2oomsorder`、`asg-api`）
3. **加密 JAR 包路径**（quickapi-client-java-*.jar 的位置）
4. **加密服务 IP**（至少一个，最多两个双机）
5. **项目构建工具**（Gradle 或 Maven）

### Step 2：实施改造

按以下顺序自动完成代码改造：

#### 2.1 引入依赖

**Gradle：**
```groovy
implementation files('lib/quickapi-client-java-x.x.x-SNAPSHOT-shaded.jar')
```

**Maven：**
```xml
<dependency>
    <groupId>org.quickssl</groupId>
    <artifactId>quickapi-client-java</artifactId>
    <version>x.x.x-SNAPSHOT</version>
    <scope>system</scope>
    <systemPath>${pom.basedir}/lib/quickapi-client-java-x.x.x-SNAPSHOT-shaded.jar</systemPath>
</dependency>
```

#### 2.2 创建加密包 `{basePackage}.encryption`

**EncryptionComponent.java** — 加密服务初始化

**EncryptionUtils.java** — 加解密工具类（3 个静态方法）：
- `encodeData(String plaintext)` — SM4/GCM PB 格式加密，返回 Base64；失败返回原文
- `deocdeData(String encodeData)` — 解密；先 isEncode 检查，非密文直接返回（兼容明文数据）
- `isEncode(String encodeData)` — 判断是否为 PB 格式密文

#### 2.3 创建 TypeHandler

**EncryptionTypeHandler.java** — 继承 `BaseTypeHandler<String>`：
- 写入时自动加密（`EncryptionUtils.encodeData`）
- 读取时自动解密（`EncryptionUtils.deocdeData`）
- **禁止** `@MappedTypes(String.class)`，必须在 Mapper XML 中显式绑定到 `_encrypt` 列

#### 2.4 实体类新增字段

每个加密字段新增对应的 `{field}Encrypt` 属性。

#### 2.5 Mapper XML 改造

**ResultMap** — 新增 `_encrypt` 列映射，绑定 TypeHandler。

**SELECT** — column list 追加 `_encrypt` 列，WHERE 条件不变。

**INSERT/UPDATE** — 追加 `_encrypt` 列，使用 TypeHandler。

#### 2.6 Service 层接入

在所有 insert/update 调用前，加入 `encryptionFieldHelper.normalizeXxxForWrite(entity)`。

#### 2.7 配置文件

各环境 bootstrap 配置添加：
```yaml
encryption:
  switch: false
  server:
    ip1: {加密服务IP1}
    ip2: {加密服务IP2}
```

### Step 3：生成 DDL

自动生成 SQL 文件（存放在 `docs/des_encrypt_columns.sql`）。

### Step 4：输出待办清单

完成后输出后续待办：
1. 确认 KEYID、生产环境 IP、证书/密钥文件部署
2. 各环境执行 DDL
3. 部署代码（switch=false），验证无回归
4. 存量数据回刷（使用 DES 回刷工具）
5. Nacos 切换 `encryption.switch=true`

## 设计原则

- **最小改动**：不引入全局拦截器，不修改现有表结构，只新增列
- **保留明文列**：迁移期间原字段不动，确保可回滚
- **单开关控制**：`encryption.switch` 一个开关管读写，Nacos 动态生效
- **TypeHandler 显式绑定**：仅作用于 `_encrypt` 列，不影响其他 String 字段
- **加密失败兜底**：`encodeData` 加密异常时返回原文，不阻断业务
