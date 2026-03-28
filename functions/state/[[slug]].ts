import { handleOgRequest } from '../_og'

export const onRequest: PagesFunction = (context) =>
  handleOgRequest(context as any, 'state')
