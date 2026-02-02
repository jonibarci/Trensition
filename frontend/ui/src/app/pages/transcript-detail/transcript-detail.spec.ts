import { ComponentFixture, TestBed } from '@angular/core/testing';

import { TranscriptDetail } from './transcript-detail';

describe('TranscriptDetail', () => {
  let component: TranscriptDetail;
  let fixture: ComponentFixture<TranscriptDetail>;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [TranscriptDetail]
    })
    .compileComponents();

    fixture = TestBed.createComponent(TranscriptDetail);
    component = fixture.componentInstance;
    await fixture.whenStable();
  });

  it('should create', () => {
    expect(component).toBeTruthy();
  });
});
