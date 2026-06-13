<script setup lang="ts">
import { reactive, ref, watch } from 'vue';
import { changePassword } from '@/api/auth';
import { formatApiError } from '@/api/errors';

const props = defineProps<{ modelValue: boolean }>();
const emit = defineEmits<{
  (e: 'update:modelValue', value: boolean): void;
}>();

const form = reactive({
  current_password: '',
  new_password: '',
  confirm: '',
});
const loading = ref(false);

watch(
  () => props.modelValue,
  (open) => {
    if (open) {
      form.current_password = '';
      form.new_password = '';
      form.confirm = '';
    }
  },
);

function close() {
  emit('update:modelValue', false);
}

async function submit() {
  if (!form.current_password) {
    ElMessage.warning('请输入当前密码');
    return;
  }
  if (form.new_password.length < 8) {
    ElMessage.warning('新密码至少 8 位');
    return;
  }
  if (form.new_password !== form.confirm) {
    ElMessage.warning('两次输入的新密码不一致');
    return;
  }
  if (form.new_password === form.current_password) {
    ElMessage.warning('新密码不能与当前密码相同');
    return;
  }
  loading.value = true;
  try {
    await changePassword({
      current_password: form.current_password,
      new_password: form.new_password,
    });
    ElMessage.success('密码已更新');
    close();
  } catch (e: unknown) {
    ElMessage.error(formatApiError(e));
  } finally {
    loading.value = false;
  }
}
</script>

<template>
  <el-dialog
    :model-value="props.modelValue"
    @update:model-value="(v: boolean) => emit('update:modelValue', v)"
    title="修改密码"
    width="420px"
    align-center
  >
    <el-form @submit.prevent="submit" label-position="top" size="default">
      <el-form-item label="当前密码">
        <el-input
          v-model="form.current_password"
          type="password"
          show-password
          autocomplete="current-password"
        />
      </el-form-item>
      <el-form-item label="新密码（至少 8 位）">
        <el-input
          v-model="form.new_password"
          type="password"
          show-password
          autocomplete="new-password"
        />
      </el-form-item>
      <el-form-item label="再次输入新密码">
        <el-input
          v-model="form.confirm"
          type="password"
          show-password
          autocomplete="new-password"
        />
      </el-form-item>
    </el-form>
    <template #footer>
      <el-button @click="close">取消</el-button>
      <el-button type="primary" :loading="loading" @click="submit">
        保存
      </el-button>
    </template>
  </el-dialog>
</template>
