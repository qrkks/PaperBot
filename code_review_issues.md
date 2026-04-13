# 代码问题检查报告

## 检查日期
2026/04/13

## 文件
`paperbot/web.py` (1884行)

## 发现的问题

### 1. 历史记录导入后缺少清理预览缓存 ⚠️

**位置**: 第1320行

**问题**:
```python
st.session_state["history_entries"] = load_history()
# 缺少: st.session_state.pop("preview_payload", None)
```

**影响**: 导入历史记录后，预览缓存仍然保留在session_state中，可能导致状态混乱。

**对比**: 第1873行在实际导入时正确清理了预览缓存。

**修复建议**:
```python
st.session_state["history_entries"] = load_history()
st.session_state.pop("preview_payload", None)  # 添加这一行
```

---

### 2. 代码重复：状态刷新逻辑重复 ⚠️

**位置**:
- 历史记录刷新: 第1183-1234行
- 预览刷新: 第1429-1480行

**问题**:
两段代码逻辑几乎完全相同，都执行相同的验证、API调用和UI更新。

**修复建议**:
提取为独立函数:
```python
def refresh_payload_records(
    *,
    payload_state_key: str,
    library_type: str,
    library_id: str,
    zotero_api_key: str,
    target_collection_path: str,
    duplicate_scope: str,
    skip_duplicates: bool,
) -> tuple[int, int, int, bool, str, int]:
    """刷新记录状态（用于历史和预览缓存）"""
    if not zotero_api_key.strip():
        st.error("Refreshing statuses requires Zotero API key.")
        st.stop()
    if not library_id:
        st.error("Current form has no valid library ID.")
        st.stop()
    try:
        validated_library_id = validate_library_id(library_type, library_id)
    except ValueError as exc:
        st.error(str(exc))
        st.stop()

    with st.spinner("Refreshing statuses using current form..."):
        try:
            return reevaluate_payload_records(
                payload_state_key=payload_state_key,
                library_type=library_type,
                library_id=validated_library_id,
                zotero_api_key=zotero_api_key.strip(),
                target_collection_path=normalize_collection_path(target_collection_path),
                duplicate_scope=duplicate_scope,
                skip_duplicates=skip_duplicates,
            )
        except Exception as exc:
            st.error(f"Failed to refresh statuses: {exc}")
            st.stop()
```

---

### 3. 代码重复：导入前状态验证重复 ⚠️

**位置**:
- 历史记录导入: 第1253-1271行
- 预览导入: 第1504-1522行

**问题**:
两段代码完全相同的验证逻辑。

**修复建议**:
同样提取为独立函数:
```python
def validate_and_refresh_payload_status(
    *,
    payload_state_key: str,
    current_evaluation_signature: str,
    library_type: str,
    library_id: str,
    zotero_api_key: str,
    target_collection_path: str,
    duplicate_scope: str,
    skip_duplicates: bool,
) -> None:
    """验证并刷新payload状态，如果不匹配则自动刷新"""
    if str(payload.get("evaluation_signature", "")) != current_evaluation_signature:
        st.warning(
            "Status markers were refreshed to match the current form. "
            "Review the list and click import again."
        )
        with st.spinner("Refreshing statuses using current form..."):
            try:
                reevaluate_payload_records(
                    payload_state_key=payload_state_key,
                    library_type=library_type,
                    library_id=validate_library_id(library_type, library_id),
                    zotero_api_key=zotero_api_key.strip(),
                    target_collection_path=normalize_collection_path(target_collection_path),
                    duplicate_scope=duplicate_scope,
                    skip_duplicates=skip_duplicates,
                )
            except Exception as exc:
                st.error(f"Failed to refresh statuses: {exc}")
                st.stop()
        st.rerun()
```

---

### 4. 潜在的API key验证不一致 ⚠️

**位置**: 多处

**问题**:
部分地方直接使用`zotero_api_key`，而不是`zotero_api_key.strip()`。

**检查结果**: 所有使用处都已经正确使用了`.strip()`，此问题不存在。

---

### 5. 干运行模式下的错误处理不一致 ⚠️

**位置**: 第1690-1697行

**分析**: 逻辑正确。在dry_run模式下会警告，在正常模式下会报错并停止。

---

### 6. 历史记录选择框的index参数问题 ⚠️

**位置**: 第1080行

**问题**:
```python
selected_history_label = st.selectbox(
    "Inspect history entry",
    options=[""] + history_labels,
    index=0,  # 问题：始终选择第一个（空字符串）
)
```

**影响**: 用户总是看到第一个历史记录，除非点击下拉框。这可能导致困惑。

**修复建议**:
```python
# 保存上一次选择的历史索引
if "selected_history_index" not in st.session_state:
    st.session_state["selected_history_index"] = 0
else:
    # 检查选中的历史是否存在
    current_index = st.session_state["selected_history_index"]
    if current_index >= len(history_entries):
        st.session_state["selected_history_index"] = 0

selected_history_label = st.selectbox(
    "Inspect history entry",
    options=[""] + history_labels,
    index=st.session_state["selected_history_index"],
    on_change=lambda: None,  # 简单的回调来更新索引
    key="history_selector",
    label_visibility="collapsed",
)
```

---

### 7. 重复的导入后保存历史记录逻辑 ⚠️

**位置**:
- 第1793行（预览模式）
- 第1871行（实际导入）

**问题**:
两处代码几乎完全相同：
```python
history_entry = build_history_entry(...)
append_history_entry(history_entry)
st.session_state["history_entries"] = load_history()
```

**修复建议**:
提取为函数:
```python
def save_to_history_and_update_session(
    *,
    history_entry: dict[str, Any],
) -> None:
    """保存历史记录到文件并更新session_state"""
    append_history_entry(history_entry)
    st.session_state["history_entries"] = load_history()
```

---

## 总结

### 严重程度
- **高优先级**:
  - 问题1: 缺少清理预览缓存
  - 问题6: 历史记录选择框问题

- **中优先级**:
  - 问题2、3: 代码重复
  - 问题7: 重复的保存历史逻辑

- **低优先级**:
  - 问题4、5: API key验证和错误处理（已正确实现）

### 建议
1. 修复历史记录导入后缺少清理预览缓存的问题
2. 修复历史记录选择框的index参数问题
3. 重构重复的状态刷新和导入验证逻辑
4. 提取重复的历史记录保存逻辑

## 测试建议

1. 测试导入历史记录后，预览缓存是否被正确清理
2. 测试历史记录选择框是否记得用户的选择
3. 测试状态刷新功能在历史和预览中是否一致
4. 测试dry_run和正常模式下的错误处理
