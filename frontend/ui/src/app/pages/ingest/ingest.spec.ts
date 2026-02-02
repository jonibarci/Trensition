import { ComponentFixture, TestBed } from '@angular/core/testing';

import { Ingest } from './ingest';

describe('Ingest', () => {
  let component: Ingest;
  let fixture: ComponentFixture<Ingest>;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [Ingest]
    })
    .compileComponents();

    fixture = TestBed.createComponent(Ingest);
    component = fixture.componentInstance;
    await fixture.whenStable();
  });

  it('should create', () => {
    expect(component).toBeTruthy();
  });
});
