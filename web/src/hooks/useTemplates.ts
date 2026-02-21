import { useQuery } from '@tanstack/react-query';
import { listTemplates } from '@/services/templateApi';

export function useTemplateList() {
  return useQuery({
    queryKey: ['templates'],
    queryFn: listTemplates,
  });
}
