import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Cpu, Terminal } from 'lucide-react';
import Admin from '../Admin';
import ExecutionLayers from './ExecutionLayers';
import { useTranslation } from 'react-i18next';

export default function ModelCenter() {
  const { t } = useTranslation('ui');
  return (
    <div className="h-full overflow-auto">
      <Tabs defaultValue="execution-layers" className="h-full">
        <div className="mb-6">
          <h1 className="text-2xl font-bold mb-4">{t("k_d19fa346f8")}</h1>
          <TabsList>
            <TabsTrigger value="execution-layers">
              <Terminal className="h-4 w-4 mr-2" />
              Harness
            </TabsTrigger>
            <TabsTrigger value="model-config">
              <Cpu className="h-4 w-4 mr-2" />
              模型配置
            </TabsTrigger>
          </TabsList>
        </div>
        <TabsContent value="model-config" className="mt-0">
          <Admin embeddedTab="model-config" />
        </TabsContent>
        <TabsContent value="execution-layers" className="mt-0">
          <ExecutionLayers />
        </TabsContent>
      </Tabs>
    </div>
  );
}
