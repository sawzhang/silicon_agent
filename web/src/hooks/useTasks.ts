import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { listTasks, createTask, cancelTask, retryTask, getTask } from '@/services/taskApi';
import type { TaskCreateRequest } from '@/types/task';

export function useTaskList(params?: {
  status?: string;
  page?: number;
  page_size?: number;
  start_date?: string;
  end_date?: string;
}) {
  return useQuery({
    queryKey: ['tasks', params],
    queryFn: () => listTasks(params),
  });
}

export function useTask(id: string) {
  return useQuery({
    queryKey: ['task', id],
    queryFn: () => getTask(id),
    enabled: !!id,
    refetchInterval: (query) => {
      const task = query.state.data;
      return task?.status === 'running' ? 3000 : false;
    },
  });
}

export function useCreateTask() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (req: TaskCreateRequest) => createTask(req),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['tasks'] });
    },
  });
}

export function useCancelTask() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => cancelTask(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['tasks'] });
      qc.invalidateQueries({ queryKey: ['cockpit'] });
    },
  });
}

export function useRetryTask() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => retryTask(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['tasks'] });
      qc.invalidateQueries({ queryKey: ['cockpit'] });
    },
  });
}
